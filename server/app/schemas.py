"""Request bodies (responses are plain dicts built in services/present.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)  # validated at registration, not at sign-in
    password: str = Field(max_length=200)


class EspnConfig(BaseModel):
    league_id: str = Field(min_length=1, max_length=20, pattern=r"^\d+$")
    espn_s2: str | None = Field(default=None, max_length=600)
    swid: str | None = Field(default=None, max_length=80)


class FlaimConfig(BaseModel):
    flaim_token: str = Field(min_length=10, max_length=4000)
    platform: Literal["espn", "yahoo", "sleeper"] = "espn"
    league_id: str = Field(min_length=1, max_length=40)


class LeagueCreate(BaseModel):
    source: Literal["espn", "demo", "flaim"]
    name: str | None = Field(default=None, max_length=120)
    season: int = Field(default=2027, ge=2018, le=2035)
    espn: EspnConfig | None = None
    flaim: FlaimConfig | None = None
    team_count: int = Field(default=10, ge=4, le=12)


class LeagueUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    sync_interval_hours: float | None = Field(default=None, ge=1, le=48)
    espn: EspnConfig | None = None


class ScoringUpdate(BaseModel):
    kind: Literal["points", "categories"] = "points"
    points: dict[str, float] = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list)
    roster_slots: dict[str, int] | None = None


class InviteCreate(BaseModel):
    expires_in_days: int | None = Field(default=14, ge=1, le=365)
    max_uses: int | None = Field(default=None, ge=1, le=100)


class ClaimTeam(BaseModel):
    team_id: int | None


class MemberUpdate(BaseModel):
    team_id: int | None = None
    role: Literal["owner", "member"] | None = None


class TradeIn(BaseModel):
    team_a: int
    team_b: int
    a_gives: list[str] = Field(default_factory=list, max_length=6)
    b_gives: list[str] = Field(default_factory=list, max_length=6)


class TradePostIn(TradeIn):
    note: str = Field(default="", max_length=500)


class OverrideIn(BaseModel):
    minutes_pct: float = Field(default=0.0, ge=-0.15, le=0.15)
    out_games: int | None = Field(default=None, ge=0, le=82)
    note: str = Field(default="", max_length=280)


class SimulateIn(BaseModel):
    team_a: int
    team_b: int
    matchup_period: int = Field(ge=1)
    exclude: list[str] = Field(default_factory=list, max_length=30)
    sims: int = Field(default=10_000, ge=500, le=20_000)
    seed: int = Field(default=2027, ge=0, le=2**31 - 1)
