from app.ai.contracts import EvalAndNext, EvalAndSummary
from app.ai.providers import normalize_model_json


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
