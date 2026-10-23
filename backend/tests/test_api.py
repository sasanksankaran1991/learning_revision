from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.security import reset_login_limits
from app.services.accounts import create_user


@pytest.fixture(autouse=True)
def fresh_db():
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_login_limits()
    get_settings().login_ip_limit = 5
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _user(username: str, password: str = "long-password", role: str = "user", must_change: bool = False):
    db = SessionLocal()
    try:
        return create_user(db, username=username, password=password, role=role, must_change_password=must_change)
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str = "long-password") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient):
    assert client.get("/api/health").json()["ok"] is True


def test_login_library_and_isolation(client: TestClient):
    _user("ada")
    _user("bea")
    ada = _login(client, "ada")
    group = client.post("/api/groups", headers=ada, json={"name": "ML & Stats", "guidance_prompt": "Interview depth"})
    assert group.status_code == 200, group.text
    group_id = group.json()["id"]
    imported = client.post(f"/api/groups/{group_id}/topics/import", headers=ada, json={"text": "KNN\nDecision Trees\n"})
    assert imported.status_code == 200
    assert len(imported.json()["created"]) == 2
    topic_id = imported.json()["created"][0]["id"]
    added = client.post(
        f"/api/topics/{topic_id}/subtopics",
        headers=ada,
        json={"name": "Distance metric", "importance": "core", "kind": "math"},
    )
    assert added.status_code == 200
    preview = client.post(f"/api/groups/{group_id}/preview-next", headers=ada)
    assert preview.status_code == 200
    assert preview.json()["outcome"] == "selected"
    bea = _login(client, "bea")
    hidden = client.get(f"/api/groups/{group_id}/topics", headers=bea)
    assert hidden.status_code == 404


def test_api_key_is_stored_encrypted(client: TestClient):
    _user("ada")
    ada = _login(client, "ada")
    created = client.post(
        "/api/me/ai-credentials",
        headers=ada,
        json={"provider": "openai", "label": "mine", "api_key": "sk-test-secret-1234", "question_model": "gpt-4o-mini"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["key_last4"] == "1234"
    assert "sk-test" not in str(body)
    listed = client.get("/api/me/ai-credentials", headers=ada).json()
    assert "api_key" not in listed[0]


def test_a_key_can_be_edited_without_replacing_the_secret(client: TestClient):
    from app.db import SessionLocal
    from app.models import AiCredential
    from app.security import decrypt_secret

    _user("ada")
    ada = _login(client, "ada")
    created = client.post(
        "/api/me/ai-credentials",
        headers=ada,
        json={"provider": "openai", "label": "mine", "api_key": "sk-test-secret-1234", "question_model": "gpt-4o-mini"},
    ).json()
    updated = client.patch(
        f"/api/me/ai-credentials/{created['id']}",
        headers=ada,
        json={"provider": "openai", "label": "revision", "api_key": "", "question_model": "gpt-4.1-mini", "eval_model": ""},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["label"] == "revision"
    assert body["question_model"] == "gpt-4.1-mini"
    assert body["eval_model"] is None
    assert body["key_last4"] == "1234"
    assert "sk-test" not in updated.text
    db = SessionLocal()
    try:
        stored = db.get(AiCredential, created["id"])
        assert decrypt_secret(stored.api_key_enc) == "sk-test-secret-1234"
    finally:
        db.close()
    replaced = client.patch(
        f"/api/me/ai-credentials/{created['id']}",
        headers=ada,
        json={"provider": "openai", "label": "revision", "api_key": "sk-replaced-9876", "question_model": "gpt-4.1-mini"},
    )
    assert replaced.status_code == 200
    assert replaced.json()["key_last4"] == "9876"
    assert replaced.json()["is_valid"] is None


def test_key_check_records_whether_the_provider_answers(client: TestClient):
    from app.ai.contracts import KeyPing
    from app.ai.gateway import reset_transport, set_transport
    from app.ai.providers import LLMResult, ProviderError

    _user("ada")
    ada = _login(client, "ada")
    created = client.post(
        "/api/me/ai-credentials",
        headers=ada,
        json={"provider": "openai", "label": "mine", "api_key": "sk-test-secret-1234", "question_model": "gpt-4o-mini"},
    ).json()

    def ok(**_kwargs):
        return LLMResult(parsed=KeyPing(ok=True), prompt_tokens=1, completion_tokens=1, cached_tokens=0, latency_ms=1)

    set_transport(ok)
    try:
        checked = client.post(f"/api/me/ai-credentials/{created['id']}/test", headers=ada)
        assert checked.status_code == 200
        assert checked.json()["is_valid"] is True
        assert "sk-test" not in checked.text

        def bad(**_kwargs):
            raise ProviderError("The model provider returned 401")

        set_transport(bad)
        failed = client.post(f"/api/me/ai-credentials/{created['id']}/test", headers=ada)
        assert failed.status_code == 200
        assert failed.json()["is_valid"] is False
        assert failed.json()["detail"] == "The model provider returned 401"
    finally:
        reset_transport()


def test_group_list_includes_a_summary(client: TestClient):
    _user("ada")
    ada = _login(client, "ada")
    group = client.post("/api/groups", headers=ada, json={"name": "Stats", "guidance_prompt": ""}).json()
    topic = client.post(f"/api/groups/{group['id']}/topics", headers=ada, json={"name": "KNN"}).json()
    created = client.post(
        f"/api/topics/{topic['id']}/subtopics",
        headers=ada,
        json={"name": "Distance", "importance": "core", "kind": "concept"},
    )
    assert created.status_code == 200
    from app.models import Topic

    db = SessionLocal()
    try:
        row = db.get(Topic, topic["id"])
        row.ewma_score = 76
        row.sessions_completed = 2
        db.commit()
    finally:
        db.close()
    listed = client.get("/api/groups", headers=ada).json()
    assert listed[0]["topic_count"] == 1
    assert listed[0]["subtopic_count"] == 1
    assert listed[0]["unanswered_count"] == 1
    assert listed[0]["average_score"] == 76
    assert listed[0]["session_count"] == 2
    assert listed[0]["active_session_id"] is None
    topics = client.get(f"/api/groups/{group['id']}/topics", headers=ada).json()
    assert topics[0]["status"] == "pending"
    assert topics[0]["subtopic_count"] == 1
    assert topics[0]["unanswered_count"] == 1


def test_a_subtopic_can_be_deleted(client: TestClient):
    _user("ada")
    ada = _login(client, "ada")
    group = client.post("/api/groups", headers=ada, json={"name": "Stats", "guidance_prompt": ""}).json()
    topic = client.post(f"/api/groups/{group['id']}/topics", headers=ada, json={"name": "KNN"}).json()
    assert topic["map_status"] == "needs_api_key"
    assert topic["subtopic_count"] == 0
    created = client.post(
        f"/api/topics/{topic['id']}/subtopics",
        headers=ada,
        json={"name": "Distance", "importance": "core", "kind": "concept"},
    )
    assert created.status_code == 200
    removed = client.delete(f"/api/topics/{topic['id']}/subtopics/{created.json()['id']}", headers=ada)
    assert removed.status_code == 200
    detail = client.get(f"/api/topics/{topic['id']}", headers=ada).json()
    assert detail["subtopics"] == []


def test_group_and_topic_can_be_deleted(client: TestClient):
    _user("ada")
    ada = _login(client, "ada")
    group = client.post("/api/groups", headers=ada, json={"name": "Stats", "guidance_prompt": "Ask why."}).json()
    topic = client.post(f"/api/groups/{group['id']}/topics", headers=ada, json={"name": "KNN"}).json()
    client.post(
        f"/api/topics/{topic['id']}/subtopics",
        headers=ada,
        json={"name": "Distance", "importance": "core", "kind": "concept"},
    )
    removed_topic = client.delete(f"/api/topics/{topic['id']}", headers=ada)
    assert removed_topic.status_code == 200
    assert client.get(f"/api/topics/{topic['id']}", headers=ada).status_code == 404
    second = client.post(f"/api/groups/{group['id']}/topics", headers=ada, json={"name": "Trees"}).json()
    removed_group = client.delete(f"/api/groups/{group['id']}", headers=ada)
    assert removed_group.status_code == 200
    assert client.get(f"/api/topics/{second['id']}", headers=ada).status_code == 404
    assert client.get("/api/groups", headers=ada).json() == []


def test_apk_login_returns_a_refresh_token(client: TestClient):
    _user("ada")
    logged_in = client.post("/api/auth/login", json={"username": "ada", "password": "long-password", "client": "apk"})
    assert logged_in.status_code == 200
    refresh = logged_in.json()["refresh_token"]
    assert refresh
    assert "refresh_token" not in logged_in.cookies
    refreshed = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"]
    assert refreshed.json()["refresh_token"] != refresh


def test_refresh_keeps_the_session(client: TestClient):
    _user("ada")
    client.post("/api/auth/login", json={"username": "ada", "password": "long-password"})
    refreshed = client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


def test_password_change_is_required_before_the_library(client: TestClient):
    _user("ada", must_change=True)
    headers = _login(client, "ada")
    blocked = client.get("/api/groups", headers=headers)
    assert blocked.status_code == 403
    profile = client.get("/api/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["must_change_password"] is True


def test_account_locks_after_repeated_failures(client: TestClient):
    get_settings().login_ip_limit = 100
    _user("ada")
    for _ in range(10):
        response = client.post("/api/auth/login", json={"username": "ada", "password": "wrong-password"})
        assert response.status_code == 401
    locked = client.post("/api/auth/login", json={"username": "ada", "password": "long-password"})
    assert locked.status_code == 429


def test_admin_creates_a_user(client: TestClient):
    _user("root", role="admin")
    headers = _login(client, "root")
    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "student", "password": "another-long-password", "must_change_password": True},
    )
    assert created.status_code == 200
    assert created.json()["role"] == "user"
    denied = _login(client, "student", "another-long-password")
    assert client.get("/api/admin/users", headers=denied).status_code == 403


def test_admin_can_promote_another_user(client: TestClient):
    _user("root", role="admin")
    _user("ada")
    headers = _login(client, "root")
    users = client.get("/api/admin/users", headers=headers).json()
    ada_id = next(user["id"] for user in users if user["username"] == "ada")
    root_id = next(user["id"] for user in users if user["username"] == "root")
    promoted = client.patch(f"/api/admin/users/{ada_id}", headers=headers, json={"role": "admin"})
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    ada = _login(client, "ada")
    assert client.get("/api/admin/users", headers=ada).status_code == 200
    demote_self = client.patch(f"/api/admin/users/{root_id}", headers=headers, json={"role": "user"})
    assert demote_self.status_code == 400
    demoted = client.patch(f"/api/admin/users/{ada_id}", headers=headers, json={"role": "user"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"


def test_env_admin_is_created_once_and_not_reset(client: TestClient, monkeypatch):
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password, verify_password
    from app.services.accounts import ensure_bootstrap_admin

    settings = get_settings()
    monkeypatch.setattr(settings, "admin_username", "boot")
    monkeypatch.setattr(settings, "admin_password", "bootstrap-pass")
    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
        ensure_bootstrap_admin(db)
        user = db.query(User).filter(User.username == "boot").one()
        assert user.role == "admin"
        user.password_hash = hash_password("changed-pass-1")
        db.commit()
        ensure_bootstrap_admin(db)
        db.refresh(user)
        assert verify_password("changed-pass-1", user.password_hash)
    finally:
        db.close()
    assert client.post("/api/auth/login", json={"username": "boot", "password": "changed-pass-1"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "boot", "password": "bootstrap-pass"}).status_code == 401


def test_usage_splits_cost_by_day_and_period(client: TestClient):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from app.db import SessionLocal
    from app.models import AiUsage

    user = _user("ada")
    ada = _login(client, "ada")
    tz = ZoneInfo("Asia/Kolkata")
    noon = datetime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)
    older = (noon - timedelta(days=3)).astimezone(timezone.utc).replace(tzinfo=None)
    recent = noon.astimezone(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        db.add(AiUsage(user_id=user.id, purpose="question", category="revision", provider="openai", model="gpt-4o-mini", total_tokens=100, cost_usd=0.01, cost_inr=1.0, created_at=recent))
        db.add(AiUsage(user_id=user.id, purpose="key_test", category="other", provider="openai", model="gpt-4o-mini", total_tokens=20, cost_usd=0.002, cost_inr=0.2, created_at=older))
        db.commit()
    finally:
        db.close()
    week = client.get("/api/me/usage?days=7", headers=ada).json()
    assert week["period"]["calls"] == 2
    assert abs(week["period"]["cost_inr"] - 1.2) < 0.001
    assert len([day for day in week["by_day"] if day["calls"]]) == 2
    today = client.get("/api/me/usage?days=1", headers=ada).json()
    assert today["period"]["calls"] == 1
    assert today["by_category"]["revision"]["calls"] == 1
    assert len(today["by_day"]) == 1
