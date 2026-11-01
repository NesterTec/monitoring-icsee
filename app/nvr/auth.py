from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from nvr.config import get_settings
from nvr.db import get_db
from nvr.models import User
from nvr.security import hash_password, verify_password


def seed_admin(db: Session) -> None:
    settings = get_settings()
    user = db.query(User).filter(User.email == settings.admin_email).one_or_none()
    if user is None:
        db.add(User(email=settings.admin_email, password_hash=hash_password(settings.admin_password)))
        db.commit()
        return
    # Keep hash if already present; do not overwrite on every boot


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
