from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .orm import League, LeagueMember, User
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_token(creds.credentials)
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
    return user


@dataclass
class Membership:
    league: League
    member: LeagueMember
    user: User

    @property
    def is_owner(self) -> bool:
        return self.member.role == "owner"


def membership(league_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Membership:
    league = db.get(League, league_id)
    member = None
    if league is not None:
        member = db.scalar(select(LeagueMember).where(LeagueMember.league_id == league_id,
                                                      LeagueMember.user_id == user.id))
    if league is None or member is None:
        # Same response for "doesn't exist" and "not a member" so league ids can't be probed.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "League not found")
    return Membership(league=league, member=member, user=user)


def owner_membership(m: Membership = Depends(membership)) -> Membership:
    if not m.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the league owner can do that")
    return m
