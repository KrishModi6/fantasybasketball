"""ESPN Fantasy Basketball connector.

Uses ESPN's v3 fantasy API (the same JSON the ESPN web app loads). It is not
officially documented, so every parse is defensive and the raw payload shape is
pinned by tests in ``tests/test_espn_parse.py``.

Public leagues need only a league id. Private leagues also need the ``espn_s2``
and ``SWID`` cookies from a browser session that can view the league.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..constants import (
    COUNTING_STATS,
    ESPN_DEFAULT_POSITION,
    ESPN_PRO_TEAMS,
    ESPN_SLOT_IDS,
    ESPN_STAT_IDS,
    BASE_POSITIONS,
)
from ..models import (
    ExpertProjection,
    FantasyTeam,
    GameLog,
    LeagueSettings,
    LeagueSnapshot,
    Matchup,
    NewsItem,
    Player,
    ProSchedule,
    RosterEntry,
    ScoringSettings,
    SeasonLine,
)
from .base import ConnectorError, ConnectorInfo, LeagueConnector

FANTASY_BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba"
SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ATHLETE_URL = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}"
EASTERN = ZoneInfo("America/New_York")

# ESPN payload stat ids we keep, as canonical keys.
_KEEP = set(COUNTING_STATS) | {"MIN", "GP"}

_INJURY_STATUS = {
    "Out": "OUT",
    "Day-To-Day": "DAY_TO_DAY",
    "Questionable": "QUESTIONABLE",
    "Doubtful": "DOUBTFUL",
    "Suspension": "SUSPENSION",
}


class RateLimiter:
    """Simple process-wide spacing between requests to the same host."""

    def __init__(self, min_interval: float = 0.35):
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


_limiter = RateLimiter()


class EspnClient:
    def __init__(self, espn_s2: str | None = None, swid: str | None = None, timeout: float = 45.0):
        cookies = {}
        if espn_s2 and swid:
            cookies = {"espn_s2": espn_s2, "SWID": swid if swid.startswith("{") else "{" + swid + "}"}
        self._http = httpx.Client(
            timeout=timeout,
            cookies=cookies,
            headers={"User-Agent": "fbb-analytics/0.1", "Accept": "application/json"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def get(self, url: str, params: Any = None, fantasy_filter: dict | None = None) -> Any:
        headers = {}
        if fantasy_filter is not None:
            headers["X-Fantasy-Filter"] = json.dumps(fantasy_filter)
        last_exc: Exception | None = None
        for attempt in range(4):
            _limiter.wait()
            try:
                r = self._http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # network blip: retry with backoff
                last_exc = exc
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = float(r.headers.get("Retry-After", 2 * (attempt + 1)))
                time.sleep(min(retry_after, 20))
                continue
            if r.status_code in (401, 403):
                raise ConnectorError(
                    "ESPN refused access. For a private league, add the espn_s2 and SWID cookies "
                    "from a logged-in browser session."
                )
            if r.status_code == 404:
                raise ConnectorError("ESPN returned 404: check the league id and season.")
            r.raise_for_status()
            # ESPN sends HTML (a login redirect) instead of JSON when cookies are stale.
            if "json" not in r.headers.get("content-type", ""):
                raise ConnectorError("ESPN returned a non-JSON response; your cookies may have expired.")
            return r.json()
        raise ConnectorError(f"ESPN request failed after retries: {last_exc or 'rate limited'}")

    # ---- endpoints ---------------------------------------------------------

    def league(self, season: int, league_id: str, views: list[str], scoring_period: int | None = None) -> dict:
        params: list[tuple[str, Any]] = [("view", v) for v in views]
        if scoring_period:
            params.append(("scoringPeriodId", scoring_period))
        return self.get(f"{FANTASY_BASE}/seasons/{season}/segments/0/leagues/{league_id}", params=params)

    def league_players(self, season: int, league_id: str, limit: int = 650, recent_games: int = 20) -> list[dict]:
        flt = _player_filter(season, limit, recent_games)
        data = self.get(
            f"{FANTASY_BASE}/seasons/{season}/segments/0/leagues/{league_id}",
            params={"view": "kona_player_info"},
            fantasy_filter=flt,
        )
        return data.get("players", [])

    def player_pool(self, season: int, limit: int = 650, recent_games: int = 20) -> list[dict]:
        """All players for a season, without a league (used for demos and history)."""
        flt = _player_filter(season, limit, recent_games)
        data = self.get(
            f"{FANTASY_BASE}/seasons/{season}/segments/0/leaguedefaults/3",
            params={"view": "kona_player_info"},
            fantasy_filter=flt,
        )
        return data.get("players", [])

    def pro_schedule(self, season: int) -> dict:
        return self.get(f"{FANTASY_BASE}/seasons/{season}", params={"view": "proTeamSchedules_wl"})

    def season_meta(self, season: int) -> dict:
        return self.get(f"{FANTASY_BASE}/seasons/{season}")

    def injuries(self) -> dict:
        return self.get(f"{SITE_BASE}/injuries")

    def news(self, limit: int = 50) -> dict:
        return self.get(f"{SITE_BASE}/news", params={"limit": limit})

    def athlete(self, athlete_id: str) -> dict:
        return self.get(ATHLETE_URL.format(id=athlete_id))


def _player_filter(season: int, limit: int, recent_games: int) -> dict:
    prev = season - 1
    return {
        "players": {
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "filterStatsForTopScoringPeriodIds": {
                "value": max(1, recent_games),
                "additionalValue": [f"00{season}", f"10{season}", f"00{prev}"],
            },
        }
    }


# ---- parsing ---------------------------------------------------------------


def _stats(raw: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (raw or {}).items():
        try:
            key = ESPN_STAT_IDS.get(int(k))
        except ValueError:
            continue
        if key in _KEEP and v is not None:
            out[key] = float(v)
    if "REB" not in out and ("OREB" in out or "DREB" in out):
        out["REB"] = out.get("OREB", 0.0) + out.get("DREB", 0.0)
    return out


def parse_player(entry: dict, season: int, period_to_season: dict[int, int] | None = None) -> Player:
    """Parse one element of a kona_player_info ``players`` array."""
    raw = entry.get("player", entry)
    pid = str(raw["id"])
    slots = [ESPN_SLOT_IDS[s] for s in raw.get("eligibleSlots", []) if s in ESPN_SLOT_IDS]
    positions = [s for s in slots if s in BASE_POSITIONS]
    if not positions:
        pos = ESPN_DEFAULT_POSITION.get(raw.get("defaultPositionId", 0))
        positions = [pos] if pos in BASE_POSITIONS else []

    history: list[SeasonLine] = []
    current: SeasonLine | None = None
    experts: list[ExpertProjection] = []
    games: list[GameLog] = []
    for s in raw.get("stats", []) or []:
        src, split, sid = s.get("statSourceId"), s.get("statSplitTypeId"), s.get("seasonId")
        st = _stats(s.get("stats"))
        if split == 0 and src == 0:
            line = SeasonLine(
                season=sid,
                team=ESPN_PRO_TEAMS.get(s.get("proTeamId") or 0, ""),
                gp=st.pop("GP", 0.0),
                minutes=st.pop("MIN", 0.0),
                stats=st,
            )
            if sid == season:
                current = line
            elif line.gp > 0:
                history.append(line)
        elif split == 0 and src == 1 and sid == season:
            gp = st.pop("GP", 0.0)
            minutes = st.pop("MIN", 0.0)
            if gp > 0:
                experts.append(
                    ExpertProjection(
                        source="ESPN",
                        season=sid,
                        gp=gp,
                        minutes_per_game=minutes / gp,
                        per_game={k: v / gp for k, v in st.items()},
                    )
                )
        elif split == 5 and src == 0:
            minutes = st.pop("MIN", 0.0)
            st.pop("GP", None)
            if minutes <= 0:
                continue  # DNP rows carry no information about rates
            games.append(
                GameLog(
                    season=sid,
                    period=s.get("scoringPeriodId", 0),
                    team=ESPN_PRO_TEAMS.get(s.get("proTeamId") or 0, ""),
                    minutes=minutes,
                    stats=st,
                )
            )
    games.sort(key=lambda g: (g.season, g.period))
    ownership = raw.get("ownership") or {}
    injury_status = raw.get("injuryStatus") or ("OUT" if raw.get("injured") else "ACTIVE")
    return Player(
        id=pid,
        name=raw.get("fullName", pid),
        pro_team=ESPN_PRO_TEAMS.get(raw.get("proTeamId", 0), "FA"),
        positions=positions,
        eligible_slots=slots,
        injury_status=injury_status,
        history=sorted(history, key=lambda h: h.season),
        current=current,
        recent_games=games,
        experts=experts,
        ownership_pct=ownership.get("percentOwned"),
        adp=ownership.get("averageDraftPosition"),
        fantasy_team_id=(entry.get("onTeamId") or None) if isinstance(entry, dict) else None,
        on_waivers=entry.get("status") == "WAIVERS",
    )


def parse_pro_schedule(data: dict, season: int) -> ProSchedule:
    games: dict[int, set[str]] = {}
    first_game: dict[int, datetime] = {}
    for team in data.get("settings", {}).get("proTeams", []):
        abbrev = ESPN_PRO_TEAMS.get(team.get("id"), team.get("abbrev", ""))
        for period, glist in (team.get("proGamesByScoringPeriod") or {}).items():
            p = int(period)
            games.setdefault(p, set()).add(abbrev)
            for g in glist:
                ts = datetime.fromtimestamp(g["date"] / 1000, tz=timezone.utc).astimezone(EASTERN)
                if p not in first_game or ts < first_game[p]:
                    first_game[p] = ts
    period_dates: dict[int, date] = {}
    if first_game:
        # Anchor on any known period, then every scoring period is one calendar day.
        anchor_p = min(first_game)
        anchor_d = first_game[anchor_p].date()
        last = max(games)
        for p in range(1, last + 1):
            period_dates[p] = anchor_d + timedelta(days=p - anchor_p)
    return ProSchedule(
        season=season,
        period_dates=period_dates,
        games={p: sorted(t) for p, t in sorted(games.items())},
    )


def parse_injury_report(data: dict) -> dict[str, dict]:
    """athlete id -> {status, note, return_date}"""
    out: dict[str, dict] = {}
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            athlete = inj.get("athlete") or {}
            aid = None
            for link in athlete.get("links", []):
                m = re.search(r"/id/(\d+)", link.get("href", ""))
                if m:
                    aid = m.group(1)
                    break
            if not aid:
                continue
            ret = (inj.get("details") or {}).get("returnDate")
            out[aid] = {
                "status": _INJURY_STATUS.get(inj.get("status", ""), (inj.get("status") or "").upper()),
                "note": inj.get("shortComment") or inj.get("longComment"),
                "return_date": date.fromisoformat(ret[:10]) if ret else None,
            }
    return out


def parse_news(data: dict) -> list[NewsItem]:
    items = []
    for a in data.get("articles", []):
        pids = [
            str(c.get("athleteId") or (c.get("athlete") or {}).get("id"))
            for c in a.get("categories", [])
            if c.get("type") == "athlete" and (c.get("athleteId") or (c.get("athlete") or {}).get("id"))
        ]
        published = a.get("published")
        items.append(
            NewsItem(
                id=str(a.get("id") or a.get("dataSourceIdentifier") or a.get("headline")),
                headline=a.get("headline", ""),
                description=a.get("description", ""),
                published=datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                player_ids=pids,
                link=((a.get("links") or {}).get("web") or {}).get("href"),
            )
        )
    return items


def parse_settings(data: dict, season: int) -> LeagueSettings:
    s = data.get("settings", {})
    sc = s.get("scoringSettings", {})
    stype = sc.get("scoringType", "H2H_POINTS")
    items = sc.get("scoringItems", [])
    if stype in ("H2H_POINTS", "POINTS"):
        points = {}
        for it in items:
            key = ESPN_STAT_IDS.get(it.get("statId"))
            if key:
                points[key] = float(it.get("points", 0))
        scoring = ScoringSettings(kind="points", points=points)
    else:
        cats = [ESPN_STAT_IDS[it["statId"]] for it in items if it.get("statId") in ESPN_STAT_IDS]
        scoring = ScoringSettings(kind="categories", categories=cats, points={})
    slots_raw = (s.get("rosterSettings") or {}).get("lineupSlotCounts", {})
    roster_slots = {
        ESPN_SLOT_IDS[int(k)]: int(v) for k, v in slots_raw.items() if int(k) in ESPN_SLOT_IDS and int(v) > 0
    }
    sched = s.get("scheduleSettings", {})
    matchup_periods = {int(k): [int(x) for x in v] for k, v in (sched.get("matchupPeriods") or {}).items()}
    reg = int(sched.get("matchupPeriodCount", 19))
    playoff_len = int(sched.get("playoffMatchupPeriodLength", 1))
    playoff_teams = int(sched.get("playoffTeamCount", 4))
    rounds = max(1, (playoff_teams - 1).bit_length())
    acq = (s.get("acquisitionSettings") or {}).get("matchupAcquisitionLimit")
    deadline = (s.get("tradeSettings") or {}).get("deadlineDate")
    return LeagueSettings(
        name=s.get("name", "ESPN League"),
        season=season,
        scoring=scoring,
        roster_slots=roster_slots or LeagueSettings.model_fields["roster_slots"].default_factory(),
        team_count=int(s.get("size") or len(data.get("teams", [])) or 10),
        regular_season_matchups=reg,
        playoff_teams=playoff_teams,
        playoff_round_weeks=[playoff_len] * rounds,
        matchup_periods=matchup_periods,
        acquisition_limit_per_matchup=int(acq) if acq not in (None, -1) else None,
        trade_deadline=datetime.fromtimestamp(deadline / 1000, tz=timezone.utc) if deadline else None,
        lineup_lock="daily",
    )


def parse_teams(data: dict) -> list[FantasyTeam]:
    members = {m.get("id"): m for m in data.get("members", [])}
    teams = []
    for t in data.get("teams", []):
        owner = None
        for oid in t.get("owners", []) or []:
            m = members.get(oid)
            if m:
                owner = m.get("displayName") or f"{m.get('firstName', '')} {m.get('lastName', '')}".strip()
                break
        name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {t['id']}"
        rec = ((t.get("record") or {}).get("overall")) or {}
        roster = [
            RosterEntry(player_id=str(e["playerId"]), slot=ESPN_SLOT_IDS.get(e.get("lineupSlotId"), "BE"))
            for e in ((t.get("roster") or {}).get("entries") or [])
        ]
        teams.append(
            FantasyTeam(
                id=int(t["id"]),
                name=name,
                abbrev=t.get("abbrev", ""),
                owner_name=owner,
                owner_ids=[str(o) for o in t.get("owners") or []],
                roster=roster,
                wins=int(rec.get("wins", 0)),
                losses=int(rec.get("losses", 0)),
                ties=int(rec.get("ties", 0)),
                points_for=float(rec.get("pointsFor", 0) or 0),
                points_against=float(rec.get("pointsAgainst", 0) or 0),
            )
        )
    return teams


def _side_points(side: dict) -> float:
    # totalPointsLive updates during the matchup; totalPoints can lag until the day closes
    # (field usage cross-checked against the open-source cwendt94/espn-api).
    live = side.get("totalPointsLive")
    return float(live if live is not None else side.get("totalPoints") or 0)


def _side_stats(side: dict) -> dict[str, float]:
    """Accrued category totals for the current matchup (category leagues)."""
    out = {}
    for sid, rec in ((side.get("cumulativeScore") or {}).get("scoreByStat") or {}).items():
        try:
            key = ESPN_STAT_IDS.get(int(sid))
        except ValueError:
            continue
        if key and isinstance(rec, dict) and rec.get("score") is not None:
            out[key] = float(rec["score"])
    return out


def parse_matchups(data: dict, regular_season_matchups: int) -> list[Matchup]:
    out = []
    for m in data.get("schedule", []):
        home = m.get("home") or {}
        away = m.get("away") or {}
        if "teamId" not in home:
            continue
        mp = int(m.get("matchupPeriodId", 0))
        out.append(
            Matchup(
                matchup_period=mp,
                home_team_id=int(home["teamId"]),
                away_team_id=int(away["teamId"]) if "teamId" in away else None,
                home_score=_side_points(home),
                away_score=_side_points(away),
                home_stats=_side_stats(home),
                away_stats=_side_stats(away),
                winner=m.get("winner", "UNDECIDED") if m.get("winner") in ("HOME", "AWAY", "TIE") else "UNDECIDED",
                is_playoff=mp > regular_season_matchups,
            )
        )
    return out


def attach_injuries(players: dict[str, Player], report: dict[str, dict]) -> None:
    for pid, info in report.items():
        p = players.get(pid)
        if not p:
            continue
        if info.get("status"):
            # The fantasy feed status wins when it's more severe (e.g. INJURY_RESERVE).
            if p.injury_status in ("ACTIVE", "", None):
                p.injury_status = info["status"]
        p.injury_note = info.get("note")
        p.expected_return = info.get("return_date")


class EspnConnector(LeagueConnector):
    key = "espn"

    def __init__(self, league_id: str, season: int, espn_s2: str | None = None, swid: str | None = None,
                 player_limit: int = 650, ages: dict[str, float] | None = None,
                 history: dict[str, list[SeasonLine]] | None = None):
        self.league_id = str(league_id).strip()
        self.season = int(season)
        self.client = EspnClient(espn_s2, swid)
        self.player_limit = player_limit
        self.ages = ages or {}
        self.history = history or {}

    @classmethod
    def info(cls) -> ConnectorInfo:
        return ConnectorInfo(
            key=cls.key,
            name="ESPN Fantasy",
            status="available",
            description="Live league, rosters, scoring settings, matchups and projections from ESPN.",
            required_fields=["league_id", "season"],
            optional_fields=["espn_s2", "swid"],
        )

    def fetch_snapshot(self) -> LeagueSnapshot:
        try:
            data = self.client.league(
                self.season, self.league_id, ["mSettings", "mTeam", "mRoster", "mMatchupScore", "mScoreboard", "mStatus", "mNav"]
            )
            settings = parse_settings(data, self.season)
            teams = parse_teams(data)
            matchups = parse_matchups(data, settings.regular_season_matchups)
            raw_players = self.client.league_players(self.season, self.league_id, self.player_limit)
            sched = parse_pro_schedule(self.client.pro_schedule(self.season), self.season)
            try:
                injuries = parse_injury_report(self.client.injuries())
                news = parse_news(self.client.news())
            except (ConnectorError, httpx.HTTPError):
                injuries, news = {}, []  # secondary data: never block a sync on it
        finally:
            self.client.close()

        players: dict[str, Player] = {}
        for entry in raw_players:
            p = parse_player(entry, self.season)
            players[p.id] = p
        # Roster entries can reference players outside the top-N pool; parse them from the roster payload.
        for t in data.get("teams", []):
            for e in ((t.get("roster") or {}).get("entries") or []):
                pid = str(e["playerId"])
                if pid not in players and e.get("playerPoolEntry"):
                    players[pid] = parse_player(e["playerPoolEntry"], self.season)
        rostered = {e.player_id: t.id for t in teams for e in t.roster}
        for pid, p in players.items():
            p.fantasy_team_id = rostered.get(pid)
            if pid in self.ages:
                p.age = self.ages[pid]
            if pid in self.history:
                known = {h.season for h in p.history}
                p.history = sorted(p.history + [h for h in self.history[pid] if h.season not in known],
                                   key=lambda h: h.season)
        attach_injuries(players, injuries)

        status = data.get("status", {})
        current_period = int(data.get("scoringPeriodId") or status.get("latestScoringPeriod") or 1)
        current_mp = int(status.get("currentMatchupPeriod") or 1)
        if not settings.matchup_periods:
            from ..schedule import build_matchup_periods

            settings.matchup_periods = build_matchup_periods(sched, settings.regular_season_matchups,
                                                             settings.playoff_round_weeks)
        return LeagueSnapshot(
            settings=settings,
            teams=teams,
            players=players,
            matchups=matchups,
            schedule=sched,
            current_period=current_period,
            current_matchup_period=current_mp,
            news=news,
            source="espn",
            fetched_at=datetime.now(timezone.utc),
        )
