from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_ready_user
from app.models import TelegramConfig, User
from app.schemas import TelegramIn, TelegramLookup
from app.security import decrypt_secret, encrypt_secret
from app.services.accounts import audit
from app.services.telegram_summary import TelegramError, lookup_chats, register_commands, send_message

router = APIRouter(prefix="/me/telegram", tags=["telegram"])


def _out(row: TelegramConfig | None) -> dict:
    if row is None:
        return {"configured": False, "chat_id": "", "enabled": False, "token_last4": "", "last_sent_on": None}
    return {
        "configured": True,
        "chat_id": row.chat_id,
        "enabled": row.enabled,
        "token_last4": row.bot_token_enc and _last4(row),
        "last_sent_on": row.last_sent_on,
    }


def _last4(row: TelegramConfig) -> str:
    try:
        token = decrypt_secret(row.bot_token_enc)
    except RuntimeError:
        return ""
    return token[-4:]


@router.get("")
def get_telegram(user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    row = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id).one_or_none()
    return _out(row)


@router.put("")
def save_telegram(body: TelegramIn, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    row = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id).one_or_none()
    if body.bot_token is None and row is None:
        raise HTTPException(status_code=400, detail="A bot token is required the first time")
    if body.chat_id is None:
        raise HTTPException(status_code=400, detail="A chat id is required")
    if row is None:
        row = TelegramConfig(user_id=user.id, bot_token_enc=encrypt_secret(body.bot_token or ""), chat_id=body.chat_id, enabled=body.enabled)
        db.add(row)
    else:
        if body.bot_token:
            row.bot_token_enc = encrypt_secret(body.bot_token)
        row.chat_id = body.chat_id
        row.enabled = body.enabled
    db.commit()
    db.refresh(row)
    audit(db, "telegram_saved", user_id=user.id, meta={"enabled": row.enabled})
    db.commit()
    if row.enabled:
        register_commands(decrypt_secret(row.bot_token_enc))
    return _out(row)


@router.post("/test")
def test_telegram(user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    row = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id).one_or_none()
    if row is None:
        raise HTTPException(status_code=400, detail="Save a bot token and chat id first")
    try:
        send_message(decrypt_secret(row.bot_token_enc), row.chat_id, "Learning revision can reach this chat.")
    except TelegramError as exc:
        audit(db, "telegram_failed", user_id=user.id, meta={"error": "test"})
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "detail": "Test message sent."}


@router.post("/chats")
def find_chats(body: TelegramLookup, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    token = body.bot_token
    if not token:
        row = db.query(TelegramConfig).filter(TelegramConfig.user_id == user.id).one_or_none()
        if row is None:
            raise HTTPException(status_code=400, detail="Paste the bot token first")
        token = decrypt_secret(row.bot_token_enc)
    try:
        chats = lookup_chats(token)
    except TelegramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"chats": chats}
