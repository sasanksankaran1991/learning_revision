from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ai.contracts import EvalAndNext, EvalAndSummary, Evaluation, MapItem, MapResult, QuestionResult, Rubric, SummaryResult
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

    def __call__(self, *, credential, model, system, user_prompt, schema, temperature, max_tokens):
        self.calls += 1
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


def test_map_is_blocked_when_the_daily_budget_is_spent(client: TestClient):
    script = Script(tokens=50)
    headers, group_id = _ready(client, script)
    client.put("/api/me/settings", headers=headers, json={"daily_token_budget": 1})
    first = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "KNN"})
    assert first.json()["map_status"] == "ready"
    second = client.post(f"/api/groups/{group_id}/topics", headers=headers, json={"name": "Trees"})
    assert second.json()["map_status"] == "budget"
