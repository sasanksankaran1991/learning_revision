from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    username: str
    password: str
    client: str = "web"

    @field_validator("client")
    @classmethod
    def client_ok(cls, value: str) -> str:
        if value not in {"web", "apk"}:
            raise ValueError("client must be web or apk")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    must_change_password: bool
    refresh_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


def _role_ok(value: str) -> str:
    if value not in {"user", "admin"}:
        raise ValueError("role must be user or admin")
    return value


class AdminCreateUser(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=200)
    timezone: str = "Asia/Kolkata"
    must_change_password: bool = True
    role: str = "user"

    @field_validator("role")
    @classmethod
    def role_ok(cls, value: str) -> str:
        return _role_ok(value)


class AdminUpdateUser(BaseModel):
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=200)
    timezone: str | None = None
    role: str | None = None

    @field_validator("role")
    @classmethod
    def role_ok(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _role_ok(value)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    timezone: str
    must_change_password: bool

    model_config = {"from_attributes": True}


class SettingsOut(BaseModel):
    questions_per_session: int
    pending_ratio: float
    w_error: float
    w_recency: float
    w_attempts: float
    min_weight_floor: float
    ucb_c: float
    max_retests_per_session: int
    daily_token_budget: int | None
    telegram_summary_time: str
    usd_inr_rate: float
    weekly_enrich_enabled: bool
    weekly_enrich_weekday: int
    weekly_enrich_time: str
    intensify_below_score: float
    max_new_subtopics: int

    model_config = {"from_attributes": True}


class SettingsUpdate(BaseModel):
    questions_per_session: int | None = Field(default=None, ge=3, le=10)
    pending_ratio: float | None = Field(default=None, ge=0, le=1)
    w_error: float | None = Field(default=None, ge=0, le=1)
    w_recency: float | None = Field(default=None, ge=0, le=1)
    w_attempts: float | None = Field(default=None, ge=0, le=1)
    min_weight_floor: float | None = Field(default=None, ge=0, le=1)
    ucb_c: float | None = Field(default=None, gt=0, le=5)
    max_retests_per_session: int | None = Field(default=None, ge=0, le=5)
    daily_token_budget: int | None = Field(default=None, ge=1)
    telegram_summary_time: str | None = None

    @field_validator("telegram_summary_time")
    @classmethod
    def summary_time_ok(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) is None:
            raise ValueError("Use a time as HH:MM")
        return value
    usd_inr_rate: float | None = Field(default=None, gt=0)
    weekly_enrich_enabled: bool | None = None
    weekly_enrich_weekday: int | None = Field(default=None, ge=0, le=6)
    weekly_enrich_time: str | None = None
    intensify_below_score: float | None = Field(default=None, ge=0, le=100)
    max_new_subtopics: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def weights_if_present(self) -> SettingsUpdate:
        weights = [self.w_error, self.w_recency, self.w_attempts]
        if any(item is not None for item in weights) and not all(item is not None for item in weights):
            raise ValueError("Send w_error, w_recency, and w_attempts together")
        if all(item is not None for item in weights) and abs(sum(weights) - 1) > 0.001:
            raise ValueError("Selection weights must sum to 1")
        return self


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    guidance_prompt: str = Field(default="", max_length=4000)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    guidance_prompt: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    guidance_prompt: str
    archived: bool
    created_at: datetime
    topic_count: int = 0
    subtopic_count: int = 0
    unanswered_count: int = 0
    average_score: int | None = None
    session_count: int = 0
    active_session_id: int | None = None

    model_config = {"from_attributes": True}


class TopicIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    notes: str = Field(default="", max_length=4000)


class TopicImport(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class TopicPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    active: bool | None = None


class SubtopicIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    importance: str = "core"
    kind: str = "concept"

    @field_validator("importance")
    @classmethod
    def importance_ok(cls, value: str) -> str:
        if value not in {"core", "supporting"}:
            raise ValueError("importance must be core or supporting")
        return value

    @field_validator("kind")
    @classmethod
    def kind_ok(cls, value: str) -> str:
        if value not in {"concept", "math", "application", "pitfall"}:
            raise ValueError("invalid kind")
        return value


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


def _provider_ok(value: str) -> str:
    if value not in {"openai", "anthropic", "gemini", "openai_compatible"}:
        raise ValueError("unsupported provider")
    return value


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class TelegramIn(BaseModel):
    bot_token: str | None = None
    chat_id: str = Field(min_length=1, max_length=64)
    enabled: bool = True

    @field_validator("bot_token", "chat_id")
    @classmethod
    def strip_telegram(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("bot_token")
    @classmethod
    def token_shape(cls, value: str | None) -> str | None:
        if value is not None and (":" not in value or not 20 <= len(value) <= 200):
            raise ValueError("That does not look like a Telegram bot token")
        return value


class TelegramLookup(BaseModel):
    bot_token: str | None = None

    @field_validator("bot_token")
    @classmethod
    def strip_lookup_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AiCredentialIn(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=80)
    api_key: str = Field(min_length=8, max_length=400)
    base_url: str | None = None
    question_model: str = Field(min_length=1, max_length=80)
    eval_model: str | None = None

    @field_validator("provider")
    @classmethod
    def provider_ok(cls, value: str) -> str:
        return _provider_ok(value)

    @field_validator("eval_model")
    @classmethod
    def eval_model_blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class AiCredentialUpdate(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=80)
    api_key: str | None = None
    base_url: str | None = None
    question_model: str = Field(min_length=1, max_length=80)
    eval_model: str | None = None

    @field_validator("provider")
    @classmethod
    def provider_ok(cls, value: str) -> str:
        return _provider_ok(value)

    @field_validator("api_key", "eval_model")
    @classmethod
    def optional_blank(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("api_key")
    @classmethod
    def api_key_length(cls, value: str | None) -> str | None:
        if value is not None and not 8 <= len(value) <= 400:
            raise ValueError("api_key must be between 8 and 400 characters")
        return value
