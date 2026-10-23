from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import ChangePasswordRequest, LoginRequest, RefreshRequest, TokenResponse
from app.security import allow_login_ip, as_utc, create_access_token, hash_password, verify_password
from app.services.accounts import audit, check_password, issue_refresh, revoke_all_refresh, revoke_refresh, rotate_refresh

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "refresh_token"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_days * 24 * 3600,
        path="/api/auth",
    )


def _token_response(user: User, refresh: str, client: str, response: Response) -> TokenResponse:
    settings = get_settings()
    if client == "web":
        _set_cookie(response, refresh)
        shown = None
    else:
        shown = refresh
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in=settings.access_token_minutes * 60,
        must_change_password=user.must_change_password,
        refresh_token=shown,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    ip = _client_ip(request)
    if not allow_login_ip(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in a minute.")
    user = db.query(User).filter(User.username == body.username.strip()).one_or_none()
    now = datetime.now(timezone.utc)
    if user and user.locked_until is not None and as_utc(user.locked_until) > now:
        raise HTTPException(status_code=429, detail="Account locked. Try again later.")
    if user is None or not user.is_active or not check_password(user, body.password):
        if user is not None and user.is_active:
            settings = get_settings()
            user.failed_logins += 1
            if user.failed_logins >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            audit(db, "login_failed", user_id=user.id, ip=ip)
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user.failed_logins = 0
    user.locked_until = None
    audit(db, "login", user_id=user.id, ip=ip, meta={"client": body.client})
    db.commit()
    refresh = issue_refresh(db, user, body.client)
    return _token_response(user, refresh, body.client, response)


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, body: RefreshRequest | None = None, db: Session = Depends(get_db)) -> TokenResponse:
    raw = (body.refresh_token if body else None) or request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(status_code=401, detail="Not signed in")
    try:
        user, secret = rotate_refresh(db, raw)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Not signed in") from exc
    client = "apk" if body and body.refresh_token else "web"
    return _token_response(user, secret, client, response)


@router.post("/logout")
def logout(request: Request, response: Response, body: RefreshRequest | None = None, db: Session = Depends(get_db)) -> dict:
    raw = (body.refresh_token if body else None) or request.cookies.get(COOKIE)
    if raw:
        revoke_refresh(db, raw)
    response.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    revoke_all_refresh(db, user.id)
    audit(db, "password_changed", user_id=user.id)
    db.commit()
    return {"ok": True}
