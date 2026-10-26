from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai.contracts import EvalAndNext, EvalAndSummary, Evaluation, MapItem, MapResult, QuestionBatch, QuestionResult, Rubric, SummaryResult
from app.ai.gateway import reset_transport, set_transport
from app.ai.providers import LLMResult
from app.db import Base, engine
from app.main import app
from app.security import reset_login_limits
from tests.test_api import _login, _user


@pytest.fixture(autouse=True)
def fresh_db():
    import app.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_login_limits()
    yield
    reset_transport()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


class Script:
    def __init__(self, tokens: int = 10):
        self.tokens = tokens
        self.calls = 0
        self.schemas: list = []

    def __call__(self, *, credential, model, system, user_prompt, schema, temperature, max_tokens, image=None):
        self.calls += 1
        self.schemas.append(schema)
        self.last_image = image
        self.last_prompt = user_prompt
        if schema is MapResult:
            parsed = MapResult(
                subtopics=[
                    MapItem(name="Bias", description="systematic error", importance="core", kind="concept"),
                    MapItem(name="Variance", description="sample sensitivity", importance="core", kind="concept"),
                ]
            )
        elif schema is QuestionResult:
            parsed = QuestionResult(
                question="Why is bias different from variance?",
                rubric=Rubric(key_points=["SECRET_POINT"]),
            )
        elif schema is QuestionBatch:
            count = sum(1 for line in user_prompt.splitlines() if line.startswith("Question "))
            bank = [
                "Why is bias different from variance?",
                "What does variance measure?",
                "How does a new sample move the fit?",
                "Where does the tradeoff show up?",
                "What happens when the model is too flexible?",
            ]
            parsed = QuestionBatch(
                questions=[
                    QuestionResult(question=bank[i % len(bank)], rubric=Rubric(key_points=["SECRET_POINT"]))
                    for i in range(count or 1)
                ]
            )
        elif schema is Evaluation:
            parsed = Evaluation(
                score=2,
                misconception="Bias is random noise",
                correction="Bias is a systematic error.",
                confidence=0.9,
            )
        elif schema is EvalAndNext:
            parsed = EvalAndNext(
                evaluation=Evaluation(
                    score=2,
                    misconception="Bias is random noise",
                    correction="Bias is a systematic error.",
                    confidence=0.9,
                ),
                next_question=QuestionResult(
                    question="What does variance measure?",
                    rubric=Rubric(key_points=["SECRET_POINT"]),
                ),
            )
        elif schema is EvalAndSummary:
            parsed = EvalAndSummary(
                evaluation=Evaluation(score=5, correction="Variance is sensitivity to the sample.", confidence=0.95),
                summary=SummaryResult(strengths=["variance"], recommendation="Revisit bias tomorrow."),
            )
        else:
            raise AssertionError(schema)
        return LLMResult(parsed, self.tokens, 5, 0, 4)


def _ready(client: TestClient, script: Script) -> dict:
    set_transport(script)
    _user("ada")
    headers = _login(client, "ada")
    created = client.post(
        "/api/me/ai-credentials",
        headers=headers,
        json={"provider": "openai", "label": "mine", "api_key": "sk-test-secret-1234", "question_model": "gpt-4o-mini"},
    )
    assert created.status_code == 200
    group = client.post("/api/groups", headers=headers, json={"name": "ML", "guidance_prompt": "Interview depth"}).json()
    return headers, group["id"]


def test_session_asks_scores_and_stops_for_the_day(client: TestClient):
    script = Script()
    headers, group_id = _ready(client, script)
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    assert topic.status_code == 200, topic.text
    assert topic.json()["map_status"] == "ready"
    detail = client.get(f"/api/topics/{topic.json()['id']}", headers=headers).json()
    assert {row["name"] for row in detail["subtopics"]} == {"Bias", "Variance"}

    client.put("/api/me/settings", headers=headers, json={"questions_per_session": 3})
    started = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["outcome"] == "selected"
    assert "SECRET_POINT" not in started.text
    session_id = body["session_id"]
    again = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers).json()
    assert again["outcome"] == "resume"
    assert again["session_id"] == session_id
    calls_after_question = script.calls

    first = client.post(
        f"/api/sessions/{session_id}/questions/1/answer",
        headers=headers,
        json={"answer": "Bias is just noise in the sample."},
    )
    assert first.status_code == 200, first.text
    assert first.json()["evaluation"]["score"] == 2
    assert first.json()["next_question"]["text"].startswith("What does variance")
    assert "SECRET_POINT" not in first.text
    repeated = client.post(
        f"/api/sessions/{session_id}/questions/1/answer",
        headers=headers,
        json={"answer": "a different answer"},
    )
    assert repeated.json()["evaluation"]["score"] == 2
    assert script.calls == calls_after_question + 1

    middle = client.post(
        f"/api/sessions/{session_id}/questions/2/answer",
        headers=headers,
        json={"answer": "Variance is scatter around the average prediction."},
    )
    assert middle.status_code == 200, middle.text
    assert middle.json()["completed"] is False

    second = client.post(
        f"/api/sessions/{session_id}/questions/3/answer",
        headers=headers,
        json={"answer": "Variance is how much the model jumps when the sample changes."},
    )
    assert second.status_code == 200, second.text
    done = second.json()
    assert done["completed"] is True
    assert done["session_score"] == 60
    assert done["mastery_level"] == "learning"
    assert "SECRET_POINT" not in second.text

    topic_after = client.get(f"/api/topics/{topic.json()['id']}", headers=headers).json()
    assert topic_after["status"] == "revised"
    assert any(row["retest_next"] for row in topic_after["subtopics"])
    blocked = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers).json()
    assert blocked["outcome"] == "nothing_today"

    usage = client.get("/api/me/usage", headers=headers).json()
    assert usage["by_category"]["first_subtopics"]["calls"] == 1
    assert usage["by_category"]["revision"]["calls"] == 4
    assert usage["today"]["total_tokens"] > 0


def test_a_photo_can_stand_in_for_the_typed_answer(client: TestClient):
    import base64

    script = Script()
    headers, group_id = _ready(client, script)
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    assert topic.status_code == 200, topic.text
    client.put("/api/me/settings", headers=headers, json={"questions_per_session": 3})
    started = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers)
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    photo = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 40).decode()
    answered = client.post(
        f"/api/sessions/{session_id}/questions/1/answer",
        headers=headers,
        json={"answer": "", "image": {"media_type": "image/jpeg", "data": photo}},
    )
    assert answered.status_code == 200, answered.text
    assert script.last_image == [("image/jpeg", photo)]
    assert "handwritten working" in script.last_prompt


def test_a_telegram_photo_is_graded_only_for_its_answer_code(client: TestClient):
    import base64

    from app.db import SessionLocal
    from app.services.telegram_summary import poll_commands, reset_sender, set_files, set_sender, set_updates
    from tests.test_telegram import TOKEN

    script = Script()
    headers, group_id = _ready(client, script)
    sent: list[str] = []
    set_sender(lambda _token, _chat, text: sent.append(text) or 81)
    client.put("/api/me/telegram", headers=headers, json={"bot_token": TOKEN, "chat_id": "4242", "enabled": True})
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    assert topic.status_code == 200, topic.text
    client.put("/api/me/settings", headers=headers, json={"questions_per_session": 3})
    started = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers)
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    try:
        created = client.post(f"/api/sessions/{session_id}/questions/1/telegram-photo", headers=headers)
        assert created.status_code == 200, created.text
        code = created.json()["code"]
        assert len(code) == 4
        assert set(code) <= set("ABCDEFGHJKMNPQRSTUVWXYZ")
        assert code in sent[0]
        again = client.post(f"/api/sessions/{session_id}/questions/1/telegram-photo", headers=headers)
        assert again.json()["code"] == code
        first = b"\xff\xd8\xff" + b"\x11" * 40
        second = b"\xff\xd8\xff" + b"\x22" * 40
        set_files(lambda _token, file_id: ("image/jpeg", second if file_id == "page2" else first))
        batches = [
            [
                {"update_id": 4, "message": {"chat": {"id": 999}, "caption": code, "photo": [{"file_id": "other"}]}},
                {"update_id": 5, "message": {"chat": {"id": 4242}, "caption": "notes", "photo": [{"file_id": "plain"}]}},
                {"update_id": 6, "message": {"chat": {"id": 4242}, "caption": code.lower(), "photo": [{"file_id": "page1"}]}},
                {"update_id": 7, "message": {"chat": {"id": 4242}, "caption": f"page {code}", "photo": [{"file_id": "page2"}]}},
            ],
            [{"update_id": 8, "message": {"chat": {"id": 4242}, "text": "DONE"}}],
        ]
        seen = {"n": 0}

        def updates(_token, _offset):
            batch = batches[min(seen["n"], 1)]
            seen["n"] += 1
            return batch

        set_updates(updates)
        calls_before = script.calls
        db = SessionLocal()
        try:
            assert poll_commands(db) == 3
            assert script.calls == calls_before
            waiting = client.get(f"/api/sessions/{session_id}/questions/1/telegram-photo", headers=headers).json()
            assert waiting["status"] == "waiting"
            assert waiting["received"] == 2
            assert poll_commands(db) == 1
        finally:
            db.close()
        assert script.calls == calls_before + 1
        assert script.last_image == [
            ("image/jpeg", base64.b64encode(first).decode()),
            ("image/jpeg", base64.b64encode(second).decode()),
        ]
        assert any("no answer code" in text for text in sent)
        assert any(f"Answer code {code} scored 2/5 from 2 photos." in text for text in sent)
        status = client.get(f"/api/sessions/{session_id}/questions/1/telegram-photo", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["status"] == "graded"
        assert status.json()["result"]["evaluation"]["score"] == 2
    finally:
        reset_sender()
        import shutil
        from app.config import ROOT

        shutil.rmtree(ROOT / "data" / "answer_photos", ignore_errors=True)


def test_skip_scores_zero_and_does_not_grade(client: TestClient):
    script = Script()
    headers, group_id = _ready(client, script)
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    assert topic.status_code == 200, topic.text
    client.put("/api/me/settings", headers=headers, json={"questions_per_session": 3})
    started = client.post(f"/api/groups/{group_id}/sessions/next", headers=headers)
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    graded = [item for item in script.schemas if item in {EvalAndNext, EvalAndSummary, Evaluation}]
    calls_before_skips = script.calls

    first = client.post(f"/api/sessions/{session_id}/questions/1/skip", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["evaluation"]["score"] == 0
    assert first.json()["completed"] is False
    assert first.json()["next_question"]["text"].startswith("What does variance")
    repeated = client.post(f"/api/sessions/{session_id}/questions/1/skip", headers=headers)
    assert repeated.json()["evaluation"]["score"] == 0

    second = client.post(f"/api/sessions/{session_id}/questions/2/skip", headers=headers)
    assert second.status_code == 200, second.text
    calls_before_last = script.calls
    third = client.post(f"/api/sessions/{session_id}/questions/3/skip", headers=headers)
    assert third.status_code == 200, third.text
    done = third.json()
    assert done["completed"] is True
    assert done["evaluation"]["score"] == 0
    assert done["session_score"] == 0
    assert script.calls == calls_before_last
    assert script.calls == calls_before_skips
    assert [item for item in script.schemas if item in {EvalAndNext, EvalAndSummary, Evaluation}] == graded
    topic_after = client.get(f"/api/topics/{topic.json()['id']}", headers=headers).json()
    assert any(row["retest_next"] for row in topic_after["subtopics"])


def test_a_practice_photo_is_accepted_for_its_answer_code(client: TestClient):
    from app.db import SessionLocal
    from app.services.telegram_summary import poll_commands, reset_sender, set_files, set_sender, set_updates
    from tests.test_telegram import TOKEN

    script = Script()
    headers, group_id = _ready(client, script)
    sent: list[str] = []
    set_sender(lambda _token, _chat, text: sent.append(text) or 81)
    client.put("/api/me/telegram", headers=headers, json={"bot_token": TOKEN, "chat_id": "4242", "enabled": True})
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    topic_id = topic.json()["id"]
    subtopic_id = client.get(f"/api/topics/{topic_id}", headers=headers).json()["subtopics"][0]["id"]
    started = client.post(f"/api/topics/{topic_id}/subtopics/{subtopic_id}/practice", headers=headers)
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    try:
        created = client.post(f"/api/sessions/{session_id}/questions/1/telegram-photo", headers=headers)
        assert created.status_code == 200, created.text
        code = created.json()["code"]
        photo = b"\xff\xd8\xff" + b"\x33" * 40
        set_files(lambda _token, _file_id: ("image/jpeg", photo))
        set_updates(lambda _token, _offset: [{"update_id": 9, "message": {"chat": {"id": 4242}, "caption": code, "photo": [{"file_id": "page"}]}}])
        db = SessionLocal()
        try:
            assert poll_commands(db) == 1
        finally:
            db.close()
        assert any(f"Saved 1 photo for {code}" in text for text in sent)
        assert not any("not part of an open session" in text for text in sent)
    finally:
        reset_sender()
        import shutil
        from app.config import ROOT

        shutil.rmtree(ROOT / "data" / "answer_photos", ignore_errors=True)


def test_asking_a_subtopic_again_leaves_the_session_score_unchanged(client: TestClient):
    script = Script()
    headers, group_id = _ready(client, script)
    topic = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Bias-variance"})
    assert topic.status_code == 200, topic.text
    topic_id = topic.json()["id"]
    detail = client.get(f"/api/topics/{topic_id}", headers=headers).json()
    subtopic_id = detail["subtopics"][0]["id"]
    before = detail["subtopics"][0]

    started = client.post(f"/api/topics/{topic_id}/subtopics/{subtopic_id}/practice", headers=headers)
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["pool"] == "practice"
    assert body["question"]["text"].startswith("Why is bias")
    session_id = body["session_id"]
    calls_after_question = script.calls

    answered = client.post(
        f"/api/sessions/{session_id}/questions/1/answer",
        headers=headers,
        json={"answer": "Bias is the systematic part."},
    )
    assert answered.status_code == 200, answered.text
    result = answered.json()
    assert result["evaluation"]["score"] == 2
    assert result["counts"] is False
    assert result["session_score"] is None
    assert result["completed"] is True
    assert script.calls == calls_after_question + 1
    assert script.schemas[-1] is Evaluation

    after = client.get(f"/api/topics/{topic_id}", headers=headers).json()["subtopics"][0]
    assert after["attempts"] == before["attempts"]
    assert after["ewma_score"] == before["ewma_score"]
    assert after["retest_next"] is False
    assert after["open_misconceptions"] == 0


def test_map_is_blocked_when_the_daily_budget_is_spent(client: TestClient):
    script = Script(tokens=50)
    headers, group_id = _ready(client, script)
    client.put("/api/me/settings", headers=headers, json={"daily_token_budget": 1})
    first = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "KNN"})
    assert first.json()["map_status"] == "ready"
    second = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Trees"})
    assert second.json()["map_status"] == "budget"
