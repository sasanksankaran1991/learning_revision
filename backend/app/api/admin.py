from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_admin
from app.models import User
from app.schemas import AdminCreateUser, AdminUpdateUser, UserOut
from app.security import hash_password
from app.services.accounts import audit, create_user, revoke_all_refresh

router = APIRouter(prefix="/admin", tags=["admin"])


def _guard_admin_change(db: Session, actor: User, target: User, *, removing_admin: bool) -> None:
    if not removing_admin:
        return
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot remove your own admin access")
    others = (
        db.query(User)
        .filter(User.role == "admin", User.is_active.is_(True), User.id != target.id)
        .count()
    )
    if others < 1:
        raise HTTPException(status_code=400, detail="Keep at least one active admin")


@router.get("/users", response_model=list[UserOut])
def list_users(_admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[User]:
    return db.query(User).order_by(User.id).all()


@router.post("/users", response_model=UserOut)
def add_user(body: AdminCreateUser, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> User:
    if db.query(User).filter(User.username == body.username.strip()).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = create_user(
        db,
        username=body.username,
        password=body.password,
        role=body.role,
        timezone=body.timezone,
        must_change_password=body.must_change_password,
    )
    audit(db, "user_created", user_id=admin.id, meta={"created_user_id": user.id})
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: AdminUpdateUser,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None and body.role != user.role:
        _guard_admin_change(db, admin, user, removing_admin=body.role != "admin")
        user.role = body.role
    if body.is_active is False and user.is_active and user.role == "admin":
        _guard_admin_change(db, admin, user, removing_admin=True)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.timezone is not None:
        user.timezone = body.timezone
    if body.password is not None:
        user.password_hash = hash_password(body.password)
        user.must_change_password = True
        db.commit()
        revoke_all_refresh(db, user.id)
    db.commit()
    audit(db, "user_updated", user_id=admin.id, meta={"target_user_id": user.id})
    db.commit()
    return user
