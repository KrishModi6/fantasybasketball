from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..orm import User
from ..schemas import LoginIn, RegisterIn
from ..config import get_settings
from ..security import RateLimiter, create_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
_login_limiter = RateLimiter(limit=10, window_s=60)
LOCAL_EMAIL = "local@courtside.app"


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if not _login_limiter.allow(f"reg:{request.client.host if request.client else '?'}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again in a minute")
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with that email already exists")
    user = User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    if not _login_limiter.allow(f"login:{request.client.host if request.client else '?'}:{body.email.lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again in a minute")
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.post("/local")
def local_sign_in(request: Request, db: Session = Depends(get_db)):
    """Desktop app: sign the single local user in without a password.

    Only enabled when the engine runs in local mode, where it listens on 127.0.0.1 only.
    """
    if not get_settings().local_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Local sign-in only works on this computer.")
    user = db.scalar(select(User).where(User.email == LOCAL_EMAIL))
    if user is None:
        user = User(email=LOCAL_EMAIL, display_name="You", password_hash="!local")  # no password can match
        db.add(user)
        db.commit()
    return {"token": create_token(user.id), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user_out(user)
