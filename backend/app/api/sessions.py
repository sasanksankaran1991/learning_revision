from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.ai.gateway import NoCredentialError
from app.ai.providers import ProviderError
from app.db import get_db
from app.deps import require_ready_user
from app.models import Group, Session as StudySession, User
from app.schemas import AnswerIn
from app.services.study import abandon_session, public_session, start_session, submit_answer

router = APIRouter(tags=["sessions"])


def _fail(exc: Exception) -> None:
    if isinstance(exc, NoCredentialError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, ProviderError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.post("/groups/{group_id}/sessions/next")
def next_session(group_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    group = db.get(Group, group_id)
    if group is None or group.user_id != user.id:
        raise HTTPException(status_code=404, detail="Group not found")
    try:
        return start_session(db, user, group)
    except (NoCredentialError, ProviderError, ValueError) as exc:
        _fail(exc)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    session = _owned(db, user, session_id)
    return public_session(db, session)


@router.post("/sessions/{session_id}/questions/{idx}/answer")
def answer(
    session_id: int,
    idx: int,
    body: AnswerIn,
    user: User = Depends(require_ready_user),
    db: Session = Depends(get_db),
) -> dict:
    session = _owned(db, user, session_id)
    try:
        return submit_answer(db, user, session, idx, body.answer)
    except (NoCredentialError, ProviderError, ValueError) as exc:
        _fail(exc)


@router.post("/sessions/{session_id}/abandon")
def abandon(session_id: int, user: User = Depends(require_ready_user), db: Session = Depends(get_db)) -> dict:
    session = _owned(db, user, session_id)
    abandon_session(db, session)
    return {"ok": True, "status": session.status}


def _owned(db: Session, user: User, session_id: int) -> StudySession:
    session = db.get(StudySession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
