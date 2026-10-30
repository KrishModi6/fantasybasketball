"""NBA.com stats connector: every shot, plus the player-tracking dashboards behind xFP.

stats.nba.com is the JSON behind NBA.com/stats. It is undocumented, wants browser-like headers, and
does not answer requests from cloud hosts (AWS, Azure, GitHub Actions all time out), so it is fetched
from a home connection: the desktop app or a script on your own machine. Every caller treats it as
optional.

Responses are {"resultSets": [{"headers": [...], "rowSet": [[...], ...]}]}; ``_rows`` turns the first
result set into dicts. Seasons use the app's ids (2026 = 2025-26) and are converted at this boundary.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date, timedelta
from typing import Any

import httpx

from ..constants import season_label
from .base import ConnectorError
from .espn import RateLimiter

log = logging.getLogger(__name__)
BASE = "https://stats.nba.com/stats"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
}
SHOT_ROW_CAP = 102_400  # shotchartdetail silently truncates at this many rows
_limiter = RateLimiter(min_interval=0.8)

# Filters every league dashboard accepts; blank means "all".
_DASH = dict(
    College="", Conference="", Country="", DateFrom="", DateTo="", Division="", DraftPick="", DraftYear="",
    GameScope="", GameSegment="", Height="", LastNGames=0, LeagueID="00", Location="", Month=0,
    OpponentTeamID=0, Outcome="", PORound=0, PerMode="Totals", Period=0, PlayerExperience="",
    PlayerOrTeam="Player", PlayerPosition="", SeasonSegment="", SeasonType="Regular Season",
    ShotClockRange="", StarterBench="", TeamID=0, VsConference="", VsDivision="", Weight="",
)
_SHOTS = dict(
    AheadBehind="", ClutchTime="", ContextFilter="", ContextMeasure="FGA", DateFrom="", DateTo="", EndPeriod="",
    EndRange="", GameID="", GameSegment="", LastNGames=0, LeagueID="00", Location="", Month=0, OpponentTeamID=0,
    Outcome="", Period=0, PlayerID=0, PlayerPosition="", PointDiff="", Position="", RangeType="", RookieYear="",
    SeasonSegment="", SeasonType="Regular Season", StartPeriod="", StartRange="", TeamID=0, VsConference="",
    VsDivision="",
)
# Hustle stats (deflections, contested shots) start with 2016-17.
FIRST_HUSTLE_SEASON = 2017
# Dashboard columns kept, by endpoint.
BOX = ("GP", "MIN", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "AST", "REB", "OREB", "DREB", "STL", "BLK", "TOV")
PASSING = ("POTENTIAL_AST", "SECONDARY_AST", "FT_AST", "PASSES_MADE")
REBOUNDING = ("REB_CHANCES", "OREB_CHANCES", "DREB_CHANCES", "REB_CONTEST")
HUSTLE = ("DEFLECTIONS", "CONTESTED_SHOTS", "CONTESTED_SHOTS_2PT", "CONTESTED_SHOTS_3PT", "BOX_OUTS")


def _mdy(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else ""


def _rows(payload: dict) -> list[dict[str, Any]]:
    sets = payload.get("resultSets") or [payload.get("resultSet") or {}]
    first = sets[0] if isinstance(sets, list) else sets
    headers = first.get("headers") or []
    return [dict(zip(headers, row)) for row in first.get("rowSet") or []]


def compact_shot(r: dict) -> list:
    """[yyyymmdd, player, game, period, seconds left in period, action, is 3, zone, feet, x, y, made]"""
    return [int(r["GAME_DATE"]), r["PLAYER_ID"], r["GAME_ID"], r["PERIOD"],
            r["MINUTES_REMAINING"] * 60 + r["SECONDS_REMAINING"], r["ACTION_TYPE"],
            int(r["SHOT_TYPE"].startswith("3")), r["SHOT_ZONE_BASIC"], r["SHOT_DISTANCE"], r["LOC_X"], r["LOC_Y"],
            r["SHOT_MADE_FLAG"]]


class NbaStatsClient:
    def __init__(self, timeout: float = 60.0, attempts: int = 4):
        self._http = httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True)
        self.attempts = attempts

    def close(self) -> None:
        self._http.close()

    def get(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        last_exc: Exception | None = None
        for attempt in range(self.attempts):
            _limiter.wait()
            try:
                r = self._http.get(f"{BASE}/{endpoint}", params=params)
            except httpx.HTTPError as exc:  # timeouts are what a blocked (cloud) address looks like
                last_exc = exc
                time.sleep(2.0 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return _rows(r.json())
        raise ConnectorError(f"NBA.com stats request failed after retries: {last_exc or 'rate limited'}")

    # ---- endpoints ---------------------------------------------------------

    def box(self, season: int, date_to: date | None = None) -> list[dict]:
        """Traditional box-score totals per player (FGM, AST, STL, ...), keyed by NBA.com player id."""
        return self.get("leaguedashplayerstats", {
            **_DASH, "Season": season_label(season), "DateTo": _mdy(date_to), "MeasureType": "Base",
            "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N", "TwoWay": 0,
        })

    def tracking(self, season: int, measure: str, date_to: date | None = None) -> list[dict]:
        """Player-tracking dashboard: measure is "Passing" (POTENTIAL_AST) or "Rebounding" (REB_CHANCES)."""
        return self.get("leaguedashptstats", {**_DASH, "Season": season_label(season), "DateTo": _mdy(date_to),
                                              "PtMeasureType": measure})

    def hustle(self, season: int, date_to: date | None = None) -> list[dict]:
        """Deflections, contested shots, box-outs (2016-17 on)."""
        if season < FIRST_HUSTLE_SEASON:
            return []
        return self.get("leaguehustlestatsplayer", {**_DASH, "Season": season_label(season), "DateTo": _mdy(date_to)})

    def dashboards(self, season: int, date_to: date | None = None) -> dict[int, dict]:
        """Box score, passing, rebounding and hustle totals through a date, merged per NBA.com player id."""
        out: dict[int, dict] = {}
        for r in self.box(season, date_to):
            out[r["PLAYER_ID"]] = {"name": r["PLAYER_NAME"], "team": r["TEAM_ABBREVIATION"],
                                   **{k: r.get(k) or 0 for k in BOX}}
        for rows, keys in ((self.tracking(season, "Passing", date_to), PASSING),
                           (self.tracking(season, "Rebounding", date_to), REBOUNDING),
                           (self.hustle(season, date_to), HUSTLE)):
            for r in rows:
                if r["PLAYER_ID"] in out:
                    out[r["PLAYER_ID"]].update({k: r.get(k) or 0 for k in keys})
        return out

    def shots(self, season: int, start: date, end: date) -> list[dict]:
        """Every field-goal attempt between two dates (inclusive), with location, type and result."""
        rows = self.get("shotchartdetail", {**_SHOTS, "Season": season_label(season),
                                            "DateFrom": _mdy(start), "DateTo": _mdy(end)})
        if len(rows) >= SHOT_ROW_CAP:
            raise ConnectorError(f"shotchartdetail hit its row cap for {start}..{end}; use a shorter window")
        return rows

    def season_shots(self, season: int, through: date | None = None, start: date | None = None,
                     window_days: int = 21) -> list[dict]:
        """A regular season's shots (from ``start``, through ``through``), fetched in windows small enough to
        stay under the row cap."""
        start = start or date(season - 1, 10, 1)
        end = min(through or date(season, 5, 31), date(season, 5, 31))  # 2020-21 ran to mid-May
        out: list[dict] = []
        while start <= end:
            stop = min(start + timedelta(days=window_days - 1), end)
            out.extend(self.shots(season, start, stop))
            start = stop + timedelta(days=1)
        return out


# ---- player ids ----------------------------------------------------------

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def name_key(name: str) -> str:
    """Accents, punctuation and suffixes dropped: 'A.J. Lawson' and 'AJ Lawson', 'Gary Payton II' and
    'Gary Payton' compare equal."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("-", " "))
    return " ".join(p for p in s.split() if p not in _SUFFIXES)


def match_ids(nba: dict[int, tuple[str, str]], espn: dict[str, tuple[str, str]]) -> dict[int, str]:
    """NBA.com player id -> ESPN id, by name, using the team to settle a shared name or a nickname.

    nba: {nba_id: (name, team)}; espn: {espn_id: (name, team)}, both from the same season.
    """
    by_name: dict[str, list[str]] = {}
    by_last_team: dict[tuple[str, str], list[str]] = {}
    for eid, (name, team) in espn.items():
        key = name_key(name)
        by_name.setdefault(key, []).append(eid)
        by_last_team.setdefault((key.split(" ")[-1], team), []).append(eid)
    out: dict[int, str] = {}
    for nid, (name, team) in nba.items():
        key = name_key(name)
        cands = by_name.get(key, [])
        if len(cands) > 1:
            cands = [e for e in cands if espn[e][1] == team]
        if not cands:  # nickname or spelling difference ("Nic" / "Nicolas"): same surname on the same team
            cands = by_last_team.get((key.split(" ")[-1], team), [])
        if len(cands) == 1:
            out[nid] = cands[0]
    return out
