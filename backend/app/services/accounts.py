from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, RefreshToken, User, UserSettings
from app.security import hash_password, hash_refresh, new_refresh_secret, refresh_expiry, verify_password
from app.security import as_utc


def audit(db: Session, action: str, *, user_id: int | None = None, ip: str | None = None, meta: dict | None = None) -> None:
    db.add(AuditLog(user_id=user_id, action=action, ip=ip, meta_json=meta))


def ensure_bootstrap_admin(db: Session) -> None:
    """Create the admin from ADMIN_USERNAME / ADMIN_PASSWORD when that username is absent.

    A later change to the password in the app is left alone. Restarting does not
    copy the env password back over the stored hash.
    """
    settings = get_settings()
    username = settings.admin_username.strip()
    password = settings.admin_password
    if not username and not password:
        return
    if not username or not password:
        raise RuntimeError("Set both ADMIN_USERNAME and ADMIN_PASSWORD, or leave both empty")
    if len(password) < 10:
        raise RuntimeError("ADMIN_PASSWORD must be at least 10 characters")
    if db.query(User).filter(User.username == username).first():
        return
    create_user(db, username=username, password=password, role="admin", must_change_password=False)


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = "user",
    timezone: str = "Asia/Kolkata",
    must_change_password: bool = False,
) -> User:
    settings = get_settings()
    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        role=role,
        timezone=timezone,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    db.add(UserSettings(user_id=user.id, usd_inr_rate=settings.usd_inr_rate))
    db.commit()
    db.refresh(user)
    return user


def issue_refresh(db: Session, user: User, device: str) -> str:
    secret = new_refresh_secret()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=secrets.token_hex(16),
            token_hash=hash_refresh(secret),
            expires_at=refresh_expiry(),
            device_label=device,
        )
    )
    db.commit()
    return secret


def rotate_refresh(db: Session, raw: str) -> tuple[User, str]:
    now = datetime.now(timezone.utc)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh(raw)).one_or_none()
    if row is None:
        raise PermissionError("invalid")
    if row.revoked_at is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        raise PermissionError("reused")
    if row.expires_at is not None and as_utc(row.expires_at) < now:
        raise PermissionError("expired")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise PermissionError("inactive")
    row.revoked_at = now
    row.last_used_at = now
    secret = new_refresh_secret()
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=row.family_id,
            token_hash=hash_refresh(secret),
            expires_at=refresh_expiry(now),
            last_used_at=now,
            device_label=row.device_label,
        )
    )
    db.commit()
    return user, secret


def revoke_refresh(db: Session, raw: str) -> None:
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_refresh(raw)).one_or_none()
    if row and row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        db.commit()


def revoke_all_refresh(db: Session, user_id: int) -> None:
    now = datetime.now(timezone.utc)
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()


def check_password(user: User, password: str) -> bool:
    return verify_password(password, user.password_hash)
