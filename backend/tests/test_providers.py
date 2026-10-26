import base64

import pytest
from pydantic import ValidationError

from app.ai.contracts import EvalAndNext, EvalAndSummary
from app.ai.providers import normalize_model_json, openai_user_content
from app.schemas import AnswerIn


def test_flat_evaluation_and_string_next_question_are_accepted():
    parsed = EvalAndNext.model_validate(
        normalize_model_json(
            EvalAndNext,
            {
                "score": "4/5",
                "correct_claims": "The split is on distance.",
                "confidence": "high",
                "next_question": "Why does k change the boundary?",
            },
        )
    )
    assert parsed.evaluation.score == 4
    assert parsed.evaluation.correct_claims == ["The split is on distance."]
    assert parsed.evaluation.confidence == 0.9
    assert parsed.next_question is not None
    assert parsed.next_question.question == "Why does k change the boundary?"


def test_summary_lists_sent_as_one_string_are_accepted():
    parsed = EvalAndSummary.model_validate(
        normalize_model_json(
            EvalAndSummary,
            {"evaluation": {"score": 2, "misconception": 0}, "summary": {"strengths": "Clear notation"}},
        )
    )
    assert parsed.evaluation.misconception is None
    assert parsed.summary.strengths == ["Clear notation"]


def test_a_photo_is_sent_as_image_input():
    content = openai_user_content("grade this", ("image/jpeg", "abc"))
    assert content[0]["text"] == "grade this"
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc"
    several = openai_user_content("grade these", [("image/jpeg", "one"), ("image/png", "two")])
    assert [item["image_url"]["url"] for item in several[1:]] == ["data:image/jpeg;base64,one", "data:image/png;base64,two"]


def test_answer_accepts_a_jpeg_and_rejects_a_blank_submission():
    raw = b"\xff\xd8\xff" + b"\x00" * 40
    data = base64.b64encode(raw).decode()
    parsed = AnswerIn.model_validate({"answer": "", "image": {"media_type": "image/jpeg", "data": data}})
    assert parsed.image is not None
    assert parsed.answer == ""
    with pytest.raises(ValidationError):
        AnswerIn.model_validate({"answer": "   "})
    with pytest.raises(ValidationError):
        AnswerIn.model_validate({"answer": "", "image": {"media_type": "image/gif", "data": data}})
