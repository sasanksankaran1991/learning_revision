from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    settings: Mapped[Optional["UserSettings"]] = relationship(back_populates="user", uselist=False)
    groups: Mapped[list[Group]] = relationship(back_populates="user")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    questions_per_session: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    pending_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.70)
    w_error: Mapped[float] = mapped_column(Float, nullable=False, default=0.50)
    w_recency: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)
    w_attempts: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    min_weight_floor: Mapped[float] = mapped_column(Float, nullable=False, default=0.05)
    ucb_c: Mapped[float] = mapped_column(Float, nullable=False, default=1.414)
    max_retests_per_session: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    daily_token_budget: Mapped[Optional[int]] = mapped_column(Integer)
    telegram_summary_time: Mapped[str] = mapped_column(String(5), nullable=False, default="23:59")
    usd_inr_rate: Mapped[float] = mapped_column(Float, nullable=False, default=84.0)
    weekly_enrich_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weekly_enrich_weekday: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    weekly_enrich_time: Mapped[str] = mapped_column(String(5), nullable=False, default="20:00")
    intensify_below_score: Mapped[float] = mapped_column(Float, nullable=False, default=80)
    max_new_subtopics: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    weekly_enrich_last_week: Mapped[Optional[str]] = mapped_column(String(8))
    default_ai_credential_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_credentials.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="settings")


class AiCredential(Base):
    __tablename__ = "ai_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(300))
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    key_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    question_model: Mapped[str] = mapped_column(String(80), nullable=False)
    eval_model: Mapped[Optional[str]] = mapped_column(String(80))
    is_valid: Mapped[Optional[bool]] = mapped_column(Boolean)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TelegramConfig(Base):
    __tablename__ = "telegram_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    bot_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sent_on: Mapped[Optional[str]] = mapped_column(String(10))
    update_offset: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    device_label: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    meta_json: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    guidance_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_credential_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_credentials.id"))
    settings_override_json: Mapped[Optional[dict]] = mapped_column(JSON)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="groups")
    topics: Mapped[list[Topic]] = relationship(back_populates="group", cascade="all, delete-orphan")


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (UniqueConstraint("group_id", "name", name="uq_topic_name_per_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mastery_level: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    ewma_score: Mapped[Optional[float]] = mapped_column(Float)
    sessions_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_session_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    interval_days: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    ease: Mapped[float] = mapped_column(Float, nullable=False, default=2.5)
    next_review_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    subtopic_map_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    group: Mapped[Group] = relationship(back_populates="topics")
    subtopics: Mapped[list[Subtopic]] = relationship(back_populates="topic", cascade="all, delete-orphan")


class Subtopic(Base):
    __tablename__ = "subtopics"
    __table_args__ = (UniqueConstraint("topic_id", "name", name="uq_subtopic_name_per_topic"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="core")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="concept")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    parent_subtopic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtopics.id"))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_in_session_id: Mapped[Optional[int]] = mapped_column(Integer)
    ewma_score: Mapped[Optional[float]] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    open_misconceptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retest_next: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    topic: Mapped[Topic] = relationship(back_populates="subtopics")


class Misconception(Base):
    __tablename__ = "misconceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    subtopic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtopics.id"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index(
            "uq_one_active_session_per_group",
            "user_id",
            "group_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    selection_pool: Mapped[str] = mapped_column(String(16), nullable=False)
    selection_prob: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    selection_debug_json: Mapped[Optional[dict]] = mapped_column(JSON)
    planned_subtopics_json: Mapped[Optional[list]] = mapped_column(JSON)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    current_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_score: Mapped[Optional[int]] = mapped_column(Integer)
    session_score: Mapped[Optional[float]] = mapped_column(Float)
    summary_json: Mapped[Optional[dict]] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    questions: Mapped[list[SessionQuestion]] = relationship(back_populates="session")


class SessionQuestion(Base):
    __tablename__ = "session_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False, index=True)
    subtopic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtopics.id"))
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    qtype: Mapped[str] = mapped_column(String(32), nullable=False, default="explain")
    is_retest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ucb_at_pick: Mapped[Optional[float]] = mapped_column(Float)
    question_text: Mapped[Optional[str]] = mapped_column(Text)
    rubric_json: Mapped[Optional[dict]] = mapped_column(JSON)
    answer_text: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    score_adjustment: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_claims_json: Mapped[Optional[list]] = mapped_column(JSON)
    incomplete_claims_json: Mapped[Optional[list]] = mapped_column(JSON)
    incorrect_claims_json: Mapped[Optional[list]] = mapped_column(JSON)
    misconception_text: Mapped[Optional[str]] = mapped_column(Text)
    correction_text: Mapped[Optional[str]] = mapped_column(Text)
    asked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    session: Mapped[Session] = relationship(back_populates="questions")


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    group_id: Mapped[Optional[int]] = mapped_column(ForeignKey("groups.id"))
    topic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("topics.id"))
    subtopic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subtopics.id"))
    next_subtopic_id: Mapped[Optional[int]] = mapped_column(Integer)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sessions.id"))
    question_id: Mapped[Optional[int]] = mapped_column(ForeignKey("session_questions.id"))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WeeklyEnrichRun(Base):
    __tablename__ = "weekly_enrich_runs"
    __table_args__ = (UniqueConstraint("user_id", "iso_week", name="uq_enrich_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    iso_week: Mapped[str] = mapped_column(String(8), nullable=False)
    topic_id: Mapped[Optional[int]] = mapped_column(ForeignKey("topics.id"))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_debug_json: Mapped[Optional[dict]] = mapped_column(JSON)
    ai_usage_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_usage.id"))
    subtopics_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
