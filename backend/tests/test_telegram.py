from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.security import reset_login_limits
from app.models import AiUsage, Group, Misconception, Session as StudySession, Subtopic, TelegramConfig, Topic, UserSettings
from app.security import decrypt_secret, encrypt_secret
from app.services.telegram_summary import (
    TelegramError,
    build_summary,
    dispatch_due,
    notify_session_finished,
    poll_commands,
    reset_sender,
    set_sender,
    set_updates,
)
from tests.test_api import _login, _user

TOKEN = "123456789:AAHexampleTokenValue1"


@pytest.fixture(autouse=True)
def fresh_db():
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_login_limits()
    yield
    reset_sender()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_bot_token_is_stored_encrypted_and_can_send_a_test(client: TestClient):
    _user("ada")
    ada = _login(client, "ada")
    saved = client.put(
        "/api/me/telegram",
        headers=ada,
        json={"bot_token": TOKEN, "chat_id": "4242", "enabled": True},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["token_last4"] == "lue1"
    assert TOKEN not in saved.text
    listed = client.get("/api/me/telegram", headers=ada)
    assert TOKEN not in listed.text
    sent: list[str] = []
    set_sender(lambda _token, _chat, text: sent.append(text))
    try:
        tested = client.post("/api/me/telegram/test", headers=ada)
        assert tested.status_code == 200
        assert sent == ["Learning revision can reach this chat."]
        assert TOKEN not in tested.text
    finally:
        reset_sender()
    db = SessionLocal()
    try:
        row = db.query(TelegramConfig).one()
        assert decrypt_secret(row.bot_token_enc) == TOKEN
    finally:
        db.close()


def test_daily_summary_sends_once_after_the_chosen_time(client: TestClient):
    user = _user("ada")
    tz = ZoneInfo("Asia/Kolkata")
    local = datetime.now(tz).replace(hour=23, minute=59, second=0, microsecond=0)
    moment = local.astimezone(timezone.utc)
    completed = moment.astimezone(timezone.utc).replace(tzinfo=None)
    tomorrow = (local + timedelta(days=1)).replace(hour=9).astimezone(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    sent: list[str] = []
    try:
        group = Group(user_id=user.id, name="ML & Stats")
        db.add(group)
        db.flush()
        topic = Topic(user_id=user.id, group_id=group.id, name="KNN <core>", next_review_at=tomorrow)
        db.add(topic)
        db.flush()
        subtopic = Subtopic(user_id=user.id, topic_id=topic.id, name="Shrinkage")
        db.add(subtopic)
        db.flush()
        db.add(StudySession(user_id=user.id, group_id=group.id, topic_id=topic.id, status="completed", selection_pool="pending", session_score=76, completed_at=completed))
        db.add(AiUsage(user_id=user.id, topic_id=topic.id, subtopic_id=subtopic.id, purpose="question", category="revision", provider="openai", model="gpt-4.1-mini", prompt_tokens=1200, completion_tokens=900, total_tokens=2100, cost_usd=0.011, cost_inr=0.92, created_at=completed))
        db.add(Misconception(user_id=user.id, topic_id=topic.id, summary="residual fitting"))
        db.add(TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(TOKEN), chat_id="4242", enabled=True))
        db.flush()
        settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
        text = build_summary(db, user, settings, moment)
        assert "Sessions: 1 · Avg score: 76" in text
        assert "Weekly subtopics: off" in text
        assert "KNN &lt;core&gt;" in text
        assert "Shrinkage (2.1k)" in text
        assert "Weak spots: residual fitting" in text
        assert "Due tomorrow: KNN &lt;core&gt;" in text
        assert "higher than 10% of the last 7 days" in text
        assert "Groups and topics:" in text
        assert text is not None
        db.commit()
        set_sender(lambda _token, _chat, message: sent.append(message))
        assert dispatch_due(db, moment) == 1
        assert dispatch_due(db, moment) == 0
        assert len(sent) == 1
        assert TOKEN not in sent[0]
    finally:
        reset_sender()
        db.close()


def test_a_quiet_day_sends_nothing(client: TestClient):
    user = _user("ada")
    db = SessionLocal()
    sent: list[str] = []
    try:
        db.add(TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(TOKEN), chat_id="4242", enabled=True))
        db.commit()
        moment = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=23, minute=59, second=30, microsecond=0).astimezone(timezone.utc)
        set_sender(lambda _token, _chat, text: sent.append(text))
        assert dispatch_due(db, moment) == 0
        assert sent == []
        assert db.query(TelegramConfig).one().last_sent_on == moment.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()
    finally:
        reset_sender()
        db.close()


def test_a_failed_send_does_not_mark_the_day_done(client: TestClient):
    user = _user("ada")
    db = SessionLocal()
    try:
        moment = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=23, minute=59, second=0, microsecond=0).astimezone(timezone.utc)
        created = moment.replace(tzinfo=None)
        db.add(AiUsage(user_id=user.id, purpose="question", category="revision", provider="openai", model="gpt-4.1-mini", total_tokens=20, cost_usd=0.001, cost_inr=0.08, created_at=created))
        db.add(TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(TOKEN), chat_id="4242", enabled=True))
        db.commit()

        def fail(_token, _chat, _text):
            raise TelegramError("Telegram rejected the message")

        set_sender(fail)
        assert dispatch_due(db, moment) == 0
        row = db.query(TelegramConfig).one()
        assert row.last_sent_on is None
    finally:
        reset_sender()
        db.close()


def test_a_finished_session_is_sent_to_the_profile_chat(client: TestClient):
    user = _user("ada")
    db = SessionLocal()
    sent: list[str] = []
    try:
        group = Group(user_id=user.id, name="Algebra")
        db.add(group)
        db.flush()
        topic = Topic(user_id=user.id, group_id=group.id, name="KNN", mastery_level="learning")
        db.add(topic)
        db.flush()
        session = StudySession(user_id=user.id, group_id=group.id, topic_id=topic.id, status="completed", selection_pool="pending", session_score=80)
        db.add(session)
        db.add(TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(TOKEN), chat_id="4242", enabled=True))
        db.commit()
        set_sender(lambda _token, _chat, text: sent.append(text))
        notify_session_finished(db, user, session, topic, {"strengths": ["recall"], "weak_subtopics": ["distance"], "recommendation": "Review distance"})
        assert len(sent) == 1
        assert "Session finished — KNN" in sent[0]
        assert "Score: 80/100" in sent[0]
        assert "Review distance" in sent[0]
        assert TOKEN not in sent[0]
    finally:
        reset_sender()
        db.close()


def test_usage_and_status_commands_reply_only_in_the_saved_chat(client: TestClient):
    user = _user("ada")
    tz = ZoneInfo("Asia/Kolkata")
    moment = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    created = moment.replace(tzinfo=None)
    db = SessionLocal()
    sent: list[tuple[str, str]] = []
    try:
        group = Group(user_id=user.id, name="ML & Stats")
        db.add(group)
        db.flush()
        topic = Topic(user_id=user.id, group_id=group.id, name="KNN", status="revised", mastery_level="learning", ewma_score=72, sessions_completed=3)
        db.add(topic)
        db.flush()
        db.add(Subtopic(user_id=user.id, topic_id=topic.id, name="Distance"))
        db.add(AiUsage(user_id=user.id, purpose="question", category="revision", provider="openai", model="gpt-4.1-mini", prompt_tokens=1200, completion_tokens=900, total_tokens=2100, cost_usd=0.011, cost_inr=0.92, created_at=created))
        db.add(TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(TOKEN), chat_id="4242", enabled=True, update_offset=0))
        db.commit()
        updates = [
            {"update_id": 7, "message": {"chat": {"id": 4242}, "text": "/usage"}},
            {"update_id": 8, "message": {"chat": {"id": 999}, "text": "/status"}},
            {"update_id": 9, "message": {"chat": {"id": 4242}, "text": "/status@revision_bot"}},
        ]
        set_updates(lambda _token, _offset: updates)
        set_sender(lambda _token, chat, text: sent.append((chat, text)))
        assert poll_commands(db, moment) == 2
        assert [chat for chat, _text in sent] == ["4242", "4242"]
        assert "Tokens: 2,100" in sent[0][1]
        assert "No tokens used today." not in sent[0][1]
        assert "ML &amp; Stats" in sent[1][1]
        assert "KNN — revised · learning · 72/100" in sent[1][1]
        assert db.query(TelegramConfig).one().update_offset == 10
        assert poll_commands(db, moment) == 0
        assert len(sent) == 2
    finally:
        reset_sender()
        db.close()
