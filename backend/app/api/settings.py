from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_ready_user
from app.models import AiUsage, User, UserSettings
from app.schemas import SettingsOut, SettingsUpdate
from app.security import as_utc

router = APIRouter(prefix="/me", tags=["me"])

CATEGORIES = ("first_subtopics", "recurring_subtopics", "revision", "other")


@router.get("", response_model=dict)
def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "timezone": user.timezone,
        "must_change_password": user.must_change_password,
    }


@router.get("/settings", response_model=SettingsOut)
def get_settings_row(user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> UserSettings:
    return db.query(UserSettings).filter(UserSettings.user_id == user.id).one()


@router.put("/settings", response_model=SettingsOut)
def update_settings(
    body: SettingsUpdate,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> UserSettings:
    row = db.query(UserSettings).filter(UserSettings.user_id == user.id).one()
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.get("/usage")
def usage(
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
    days: int = Query(default=7, ge=1, le=366),
    start: str | None = None,
    end: str | None = None,
) -> dict:
    tz = ZoneInfo(user.timezone)
    today_local = datetime.now(tz).date()
    if start or end:
        if not start or not end:
            raise HTTPException(status_code=400, detail="Send both start and end dates")
        start_day = _parse_day(start)
        end_day = _parse_day(end)
    else:
        end_day = today_local
        start_day = end_day - timedelta(days=days - 1)
    if end_day < start_day:
        raise HTTPException(status_code=400, detail="The end date is before the start date")
    if (end_day - start_day).days > 366:
        raise HTTPException(status_code=400, detail="Choose a period of at most 366 days")

    rows = db.query(AiUsage).filter(AiUsage.user_id == user.id).all()
    by_category = {name: _empty_bucket() for name in CATEGORIES}
    by_day = {
        (start_day + timedelta(days=offset)).isoformat(): {
            "date": (start_day + timedelta(days=offset)).isoformat(),
            **_empty_bucket(),
            "by_category": {name: _empty_bucket() for name in CATEGORIES},
        }
        for offset in range((end_day - start_day).days + 1)
    }
    midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    today = _empty_bucket()
    for row in rows:
        moment = as_utc(row.created_at)
        if moment >= midnight:
            _add(today, row)
        local_day = moment.astimezone(tz).date()
        if local_day < start_day or local_day > end_day:
            continue
        category = row.category if row.category in by_category else "other"
        _add(by_category[category], row)
        day = by_day[local_day.isoformat()]
        _add(day, row)
        _add(day["by_category"][category], row)
    period = _empty_bucket()
    for bucket in by_category.values():
        for key in period:
            period[key] += bucket[key]
    return {
        "timezone": user.timezone,
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
        "today": {"total_tokens": today["total_tokens"], "cost_usd": today["cost_usd"], "cost_inr": today["cost_inr"]},
        "period": period,
        "by_category": by_category,
        "by_day": list(by_day.values()),
    }


def _parse_day(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use dates as YYYY-MM-DD") from exc


def _empty_bucket() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "cost_inr": 0.0}


def _add(bucket: dict, row: AiUsage) -> None:
    bucket["calls"] += 1
    bucket["prompt_tokens"] += row.prompt_tokens
    bucket["completion_tokens"] += row.completion_tokens
    bucket["total_tokens"] += row.total_tokens
    bucket["cost_usd"] += row.cost_usd
    bucket["cost_inr"] += row.cost_inr
