from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.contracts import KeyPing
from app.ai.gateway import complete
from app.ai.providers import ProviderError
from app.db import get_db
from app.deps import require_ready_user
from app.models import AiCredential, User
from app.schemas import AiCredentialIn, AiCredentialUpdate
from app.security import encrypt_secret, validate_base_url
from app.services.accounts import audit

router = APIRouter(prefix="/me/ai-credentials", tags=["ai"])


def _out(row: AiCredential) -> dict:
    return {
        "id": row.id,
        "provider": row.provider,
        "label": row.label,
        "base_url": row.base_url,
        "key_last4": row.key_last4,
        "question_model": row.question_model,
        "eval_model": row.eval_model,
        "is_valid": row.is_valid,
    }


@router.get("")
def list_credentials(user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(AiCredential).filter(AiCredential.user_id == user.id).order_by(AiCredential.id).all()
    return [_out(row) for row in rows]


@router.post("")
def add_credential(body: AiCredentialIn, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    base_url = None
    if body.provider == "openai_compatible":
        if not body.base_url:
            raise HTTPException(status_code=400, detail="base_url is required for an OpenAI-compatible endpoint")
        try:
            base_url = validate_base_url(body.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = AiCredential(
        user_id=user.id,
        provider=body.provider,
        label=body.label,
        base_url=base_url,
        api_key_enc=encrypt_secret(body.api_key),
        key_last4=body.api_key[-4:],
        question_model=body.question_model,
        eval_model=body.eval_model,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit(db, "key_added", user_id=user.id, meta={"credential_id": row.id, "provider": row.provider})
    db.commit()
    return _out(row)


@router.patch("/{credential_id}")
def update_credential(
    credential_id: int,
    body: AiCredentialUpdate,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AiCredential, credential_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    base_url = None
    if body.provider == "openai_compatible":
        if not body.base_url:
            raise HTTPException(status_code=400, detail="base_url is required for an OpenAI-compatible endpoint")
        try:
            base_url = validate_base_url(body.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    row.provider = body.provider
    row.label = body.label.strip()
    row.base_url = base_url
    row.question_model = body.question_model.strip()
    row.eval_model = body.eval_model
    if body.api_key:
        row.api_key_enc = encrypt_secret(body.api_key)
        row.key_last4 = body.api_key[-4:]
        row.is_valid = None
    db.commit()
    db.refresh(row)
    audit(db, "key_updated", user_id=user.id, meta={"credential_id": row.id, "provider": row.provider})
    db.commit()
    return _out(row)


@router.post("/{credential_id}/test")
def test_credential(
    credential_id: int,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AiCredential, credential_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    try:
        complete(
            db,
            user,
            credential=row,
            purpose="key_test",
            schema=KeyPing,
            system="Reply with one JSON object and no markdown fence.",
            user_prompt='Return {"ok": true}.',
            temperature=0,
            max_tokens=30,
            allow_over_budget=True,
        )
        row.is_valid = True
        detail = "This key responded."
    except ProviderError as exc:
        row.is_valid = False
        detail = str(exc)
    except Exception:
        row.is_valid = False
        detail = "This key did not respond."
    db.commit()
    body = _out(row)
    body["detail"] = detail
    return body


@router.delete("/{credential_id}")
def delete_credential(
    credential_id: int,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(AiCredential, credential_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Credential not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
