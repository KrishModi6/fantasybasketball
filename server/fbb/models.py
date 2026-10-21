"""Provider-neutral league data model.

Every connector (ESPN, Flaim, file import, demo) produces a ``LeagueSnapshot``.
The analytics engine only ever reads snapshots, so adding a data source never
touches the math.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from .constants import (
    DEFAULT_CATEGORIES,
    DEFAULT_POINTS_SCORING,
    DEFAULT_ROSTER_SLOTS,
    DERIVED_STATS,
)


class ScoringSettings(BaseModel):
    kind: Literal["points", "categories"] = "points"
    # Points leagues: stat key -> fantasy points per unit.
    points: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_POINTS_SCORING))
    # Category leagues: ordered list of category keys (e.g. "FG%", "3PM", "TO").
    categories: list[str] = Field(default_factory=lambda: list(DEFAULT_CATEGORIES))

    def fantasy_points(self, stats: dict[str, float]) -> float:
        total = 0.0
        for key, weight in self.points.items():
            if key in stats:
                total += weight * stats[key]
            elif key in DERIVED_STATS:
                total += weight * DERIVED_STATS[key](stats)
        return total

    def breakdown(self, stats: dict[str, float]) -> dict[str, float]:
        """Fantasy points contributed by each scoring stat."""
        out: dict[str, float] = {}
        for key, weight in self.points.items():
            value = stats.get(key)
            if value is None and key in DERIVED_STATS:
                value = DERIVED_STATS[key](stats)
            out[key] = weight * (value or 0.0)
        return out


class SeasonLine(BaseModel):
    """Totals for one player-season (or season-to-date)."""
    season: int
    team: str = ""
    gp: float = 0
    minutes: float = 0
    stats: dict[str, float] = Field(default_factory=dict)  # season totals


class GameLog(BaseModel):
    season: int
    period: int  # provider scoring period (one per calendar day)
    team: str = ""
    minutes: float = 0
    stats: dict[str, float] = Field(default_factory=dict)


class ExpertProjection(BaseModel):
    """A published projection (ESPN, or uploaded from another site)."""
    source: str
    season: int
    gp: float
    minutes_per_game: float
    per_game: dict[str, float]


class Player(BaseModel):
    id: str
    name: str
    pro_team: str = "FA"
    positions: list[str] = Field(default_factory=list)
    eligible_slots: list[str] = Field(default_factory=list)
    injury_status: str = "ACTIVE"
    injury_note: str | None = None
    expected_return: date | None = None
    age: float | None = None
    debut_year: int | None = None
    history: list[SeasonLine] = Field(default_factory=list)  # completed seasons
    current: SeasonLine | None = None  # season-to-date
    recent_games: list[GameLog] = Field(default_factory=list)
    experts: list[ExpertProjection] = Field(default_factory=list)
    ownership_pct: float | None = None
    adp: float | None = None
    fantasy_team_id: int | None = None  # None -> free agent
    on_waivers: bool = False


class RosterEntry(BaseModel):
    player_id: str
    slot: str = "BE"


class FantasyTeam(BaseModel):
    id: int
    name: str
    abbrev: str = ""
    owner_name: str | None = None
    roster: list[RosterEntry] = Field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0
    points_against: float = 0


class Matchup(BaseModel):
    matchup_period: int
    home_team_id: int
    away_team_id: int | None = None  # None -> bye
    home_score: float = 0
    away_score: float = 0
    winner: Literal["HOME", "AWAY", "TIE", "UNDECIDED"] = "UNDECIDED"
    is_playoff: bool = False
    # Category leagues: totals accrued so far this matchup (stat key -> value), when the source provides them.
    home_stats: dict[str, float] = Field(default_factory=dict)
    away_stats: dict[str, float] = Field(default_factory=dict)


class ProSchedule(BaseModel):
    """NBA schedule indexed by scoring period (one period per calendar day)."""
    season: int
    period_dates: dict[int, date] = Field(default_factory=dict)
    games: dict[int, list[str]] = Field(default_factory=dict)  # period -> teams playing

    def teams_playing(self, period: int) -> list[str]:
        return self.games.get(period, [])

    def team_games_between(self, team: str, start: int, end: int) -> int:
        return sum(1 for p in range(start, end + 1) if team in self.games.get(p, ()))


class LeagueSettings(BaseModel):
    name: str
    season: int
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    roster_slots: dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_ROSTER_SLOTS))
    team_count: int = 10
    regular_season_matchups: int = 19
    playoff_teams: int = 4
    playoff_round_weeks: list[int] = Field(default_factory=lambda: [2, 2])
    # matchup period -> scoring periods (days) it spans
    matchup_periods: dict[int, list[int]] = Field(default_factory=dict)
    acquisition_limit_per_matchup: int | None = 7
    trade_deadline: datetime | None = None
    lineup_lock: Literal["daily", "weekly"] = "daily"
    playoff_seeding_tiebreaker: str = "H2H_RECORD"


class NewsItem(BaseModel):
    id: str
    headline: str
    description: str = ""
    published: datetime | None = None
    player_ids: list[str] = Field(default_factory=list)
    source: str = "ESPN"
    link: str | None = None


class PlayerOverride(BaseModel):
    """A bounded, manual adjustment entered by a league member (e.g. a role change)."""
    player_id: str
    minutes_pct: float = 0.0  # clamped to +/-15%
    out_games: int | None = None  # override expected games missed
    note: str = ""


class LeagueSnapshot(BaseModel):
    settings: LeagueSettings
    teams: list[FantasyTeam]
    players: dict[str, Player]
    matchups: list[Matchup] = Field(default_factory=list)
    schedule: ProSchedule
    current_period: int = 1  # today's scoring period
    current_matchup_period: int = 1
    news: list[NewsItem] = Field(default_factory=list)
    overrides: list[PlayerOverride] = Field(default_factory=list)
    source: str = "manual"
    fetched_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)

    def team(self, team_id: int) -> FantasyTeam:
        for t in self.teams:
            if t.id == team_id:
                return t
        raise KeyError(f"team {team_id} not in league")

    def roster_ids(self, team_id: int) -> list[str]:
        return [e.player_id for e in self.team(team_id).roster if e.player_id in self.players]

    def free_agents(self) -> list[Player]:
        rostered = {e.player_id for t in self.teams for e in t.roster}
        return [p for pid, p in self.players.items() if pid not in rostered]
