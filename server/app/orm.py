"""Database tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSettings(Base):
    """Per-user secrets, such as the user's own OpenAI key (Fernet-encrypted JSON).

    A separate table so existing databases pick it up through create_all, with no migration.
    """
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class League(Base):
    __tablename__ = "leagues"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    season: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))  # espn | demo | file | flaim
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    connector_config: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # Fernet-encrypted JSON
    sync_interval_hours: Mapped[float] = mapped_column(Float, default=1.0)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(20), default="never")  # ok | error | running | never
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    members: Mapped[list["LeagueMember"]] = relationship(back_populates="league", cascade="all, delete-orphan")


class LeagueMember(Base):
    __tablename__ = "league_members"
    __table_args__ = (UniqueConstraint("league_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # owner | member
    team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    league: Mapped[League] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Snapshot(Base):
    """A full league state at one point in time (zlib-compressed JSON)."""
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    source: Mapped[str] = mapped_column(String(20))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class ProjectionHistory(Base):
    """Daily time series of each player's projection inside a league (one row per player-day)."""
    __tablename__ = "projection_history"
    __table_args__ = (
        UniqueConstraint("league_id", "player_id", "as_of"),
        Index("ix_projhist_player_time", "league_id", "player_id", "as_of"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"))
    player_id: Mapped[str] = mapped_column(String(20))
    as_of: Mapped[date] = mapped_column(Date)
    fp_pg: Mapped[float] = mapped_column(Float)
    mpg: Mapped[float] = mapped_column(Float)
    availability: Mapped[float] = mapped_column(Float)
    games_out: Mapped[float] = mapped_column(Float, default=0.0)
    injury_status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class PlayerOverrideRow(Base):
    __tablename__ = "player_overrides"
    __table_args__ = (UniqueConstraint("league_id", "player_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[str] = mapped_column(String(20))
    minutes_pct: Mapped[float] = mapped_column(Float, default=0.0)
    out_games: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(280), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class TradePost(Base):
    """A trade shared to the league board, with the evaluation frozen at posting time."""
    __tablename__ = "trade_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    team_a: Mapped[int] = mapped_column(Integer)
    team_b: Mapped[int] = mapped_column(Integer)
    a_gives: Mapped[list] = mapped_column(JSON)
    b_gives: Mapped[list] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(500), default="")
    evaluation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    author: Mapped[User] = relationship()


class Activity(Base):
    __tablename__ = "activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
