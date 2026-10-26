from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.ai.pricing import cost_usd
from app.ai.providers import LLMResult, http_transport
from app.models import AiCredential, AiUsage, Group, User, UserSettings
from app.security import PURPOSE_CATEGORY, as_utc

_transport = http_transport


class BudgetError(Exception):
    pass


class NoCredentialError(Exception):
    pass


def set_transport(fn) -> None:
    global _transport
    _transport = fn


def reset_transport() -> None:
    global _transport
    _transport = http_transport


def resolve_credential(db: Session, user: User, group: Group | None = None) -> AiCredential | None:
    if group is not None and group.ai_credential_id:
        row = db.get(AiCredential, group.ai_credential_id)
        if row is not None and row.user_id == user.id:
            return row
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    if settings.default_ai_credential_id:
        row = db.get(AiCredential, settings.default_ai_credential_id)
        if row is not None and row.user_id == user.id:
            return row
    return db.query(AiCredential).filter(AiCredential.user_id == user.id).order_by(AiCredential.id.desc()).first()


def budget_blocked(db: Session, user: User, settings: UserSettings | None = None) -> bool:
    settings = settings or db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    if settings.daily_token_budget is None:
        return False
    tz = ZoneInfo(user.timezone)
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = db.query(AiUsage).filter(AiUsage.user_id == user.id).all()
    used = 0
    for row in rows:
        created = as_utc(row.created_at)
        if created >= start:
            used += row.total_tokens
    return used >= settings.daily_token_budget


def complete(
    db: Session,
    user: User,
    *,
    credential: AiCredential,
    purpose: str,
    schema,
    system: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 900,
    allow_over_budget: bool = False,
    group_id: int | None = None,
    topic_id: int | None = None,
    subtopic_id: int | None = None,
    session_id: int | None = None,
    question_id: int | None = None,
    image: tuple[str, str] | None = None,
) -> LLMResult:
    settings = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    if not allow_over_budget and budget_blocked(db, user, settings):
        raise BudgetError("Daily token budget is used up. A session already in progress can still be finished.")
    chosen = model or credential.question_model
    try:
        result = _transport(
            credential=credential,
            model=chosen,
            system=system,
            user_prompt=user_prompt,
            schema=schema,
            temperature=temperature,
            max_tokens=max_tokens,
            image=image,
        )
        _record(db, user, settings, credential, chosen, purpose, result, True, None, group_id, topic_id, subtopic_id, session_id, question_id)
        return result
    except BudgetError:
        raise
    except Exception as exc:
        empty = LLMResult(parsed=schema(), prompt_tokens=0, completion_tokens=0, cached_tokens=0, latency_ms=0)
        _record(db, user, settings, credential, chosen, purpose, empty, False, type(exc).__name__, group_id, topic_id, subtopic_id, session_id, question_id)
        raise


def _record(db, user, settings, credential, model, purpose, result: LLMResult, success: bool, error_code, group_id, topic_id, subtopic_id, session_id, question_id) -> None:
    usd = cost_usd(model, result.prompt_tokens, result.completion_tokens) if success else 0
    db.add(
        AiUsage(
            user_id=user.id,
            group_id=group_id,
            topic_id=topic_id,
            subtopic_id=subtopic_id,
            session_id=session_id,
            question_id=question_id,
            purpose=purpose,
            category=PURPOSE_CATEGORY.get(purpose, "other"),
            provider=credential.provider,
            model=model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cached_tokens=result.cached_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
            cost_usd=usd,
            cost_inr=usd * settings.usd_inr_rate,
            latency_ms=result.latency_ms,
            success=success,
            error_code=error_code,
            prompt_version="v1",
        )
    )
    db.commit()
