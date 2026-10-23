from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class KeyPing(BaseModel):
    ok: bool = True


class MapItem(BaseModel):
    name: str
    description: str = ""
    importance: str = "core"
    kind: str = "concept"


class MapResult(BaseModel):
    subtopics: list[MapItem] = Field(default_factory=list)


class Rubric(BaseModel):
    key_points: list[str] = Field(default_factory=list)
    common_errors: list[str] = Field(default_factory=list)
    max_score: int = 5


class QuestionResult(BaseModel):
    question: str = ""
    qtype: str = "explain"
    rubric: Rubric = Field(default_factory=Rubric)


class Evaluation(BaseModel):
    score: int = 0
    correct_claims: list[str] = Field(default_factory=list)
    incomplete_claims: list[str] = Field(default_factory=list)
    incorrect_claims: list[str] = Field(default_factory=list)
    math_errors: list[str] = Field(default_factory=list)
    misconception: str | None = None
    correction: str = ""
    confidence: float = 1

    @field_validator("misconception", mode="before")
    @classmethod
    def misconception_text(cls, value):
        if value in (None, "", 0, False, "0", "none", "None", "null"):
            return None
        if isinstance(value, list):
            text = "; ".join(str(item) for item in value if item not in (None, "", 0))
            return text or None
        if not isinstance(value, str):
            return str(value)
        return value

    @field_validator("correction", mode="before")
    @classmethod
    def correction_text(cls, value):
        if value in (None, 0, False):
            return ""
        if isinstance(value, list):
            return "; ".join(str(item) for item in value)
        return value if isinstance(value, str) else str(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_number(cls, value):
        if isinstance(value, str):
            named = {"high": 0.9, "medium": 0.6, "low": 0.3}
            return named.get(value.strip().lower(), value)
        return value


class EvalAndNext(BaseModel):
    evaluation: Evaluation = Field(default_factory=Evaluation)
    next_question: QuestionResult | None = None


class SummaryResult(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weak_subtopics: list[str] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    recommendation: str = ""

    @field_validator("recommendation", mode="before")
    @classmethod
    def recommendation_text(cls, value):
        if value in (None, 0, False):
            return ""
        if isinstance(value, list):
            return "; ".join(str(item) for item in value)
        return value if isinstance(value, str) else str(value)


class EvalAndSummary(BaseModel):
    evaluation: Evaluation = Field(default_factory=Evaluation)
    summary: SummaryResult = Field(default_factory=SummaryResult)
