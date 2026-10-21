"""Demo league: a realistic 10-team league drafted from the live ESPN player pool.

Lets anyone explore every feature without ESPN credentials. Uses the reference
league's settings (ESPN default H2H points, 19 weekly matchups, 4-team playoffs
with two-week rounds, daily lineups).
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from ..constants import DEFAULT_POINTS_SCORING, DEFAULT_ROSTER_SLOTS
from ..history import DATA_DIR
from ..models import FantasyTeam, LeagueSettings, LeagueSnapshot, Matchup, RosterEntry, ScoringSettings
from ..schedule import build_matchup_periods
from .base import ConnectorError, ConnectorInfo, LeagueConnector
from .espn import EspnClient, parse_injury_report, parse_news, parse_player, parse_pro_schedule, attach_injuries

CACHE_DIR = DATA_DIR / "cache"
POOL_TTL = 6 * 3600

TEAM_NAMES = [
    ("Pick and Roll Call", "PRC"), ("Glass Cleaners", "GLS"), ("Corner Three Collective", "C3C"),
    ("Hack-a-Shaq Attack", "HAS"), ("The Euro Steppers", "EUR"), ("Bench Mob", "BNC"),
    ("Swish Army", "SWA"), ("Paint Protectors", "PNT"), ("Fast Break Fund", "FBF"),
    ("Moneyball Bigs", "MBB"), ("Iso Kings", "ISO"), ("Zone Busters", "ZBU"),
]
OWNERS = ["Alex", "Jordan", "Sam", "Riley", "Taylor", "Morgan", "Casey", "Jamie", "Avery", "Quinn", "Drew", "Rowan"]


def _cached(name: str, ttl: float, fetch):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return json.loads(path.read_text())
    data = fetch()
    path.write_text(json.dumps(data))
    return data


class DemoConnector(LeagueConnector):
    key = "demo"

    def __init__(self, season: int = 2027, team_count: int = 10, seed: int = 7, name: str = "Demo League"):
        self.season = season
        self.team_count = max(4, min(team_count, len(TEAM_NAMES)))
        self.seed = seed
        self.name = name

    @classmethod
    def info(cls) -> ConnectorInfo:
        return ConnectorInfo(key=cls.key, name="Demo league", status="available",
                             description="A 10-team league drafted by ADP from the live ESPN player pool.")

    def fetch_snapshot(self) -> LeagueSnapshot:
        client = EspnClient()
        try:
            pool = _cached(f"pool_{self.season}.json", POOL_TTL,
                           lambda: client.player_pool(self.season, limit=650, recent_games=20))
            sched_raw = _cached(f"schedule_{self.season}.json", POOL_TTL, lambda: client.pro_schedule(self.season))
            meta = _cached(f"meta_{self.season}.json", 3600, lambda: client.season_meta(self.season))
            try:
                injuries = parse_injury_report(_cached("injuries.json", 1800, client.injuries))
                news = parse_news(_cached("news.json", 1800, client.news))
            except Exception:  # noqa: BLE001
                injuries, news = {}, []
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError(f"Could not reach ESPN to build the demo league: {exc}") from exc
        finally:
            client.close()

        players = {}
        for entry in pool:
            p = parse_player(entry, self.season)
            p.fantasy_team_id = None
            players[p.id] = p
        attach_injuries(players, injuries)
        sched = parse_pro_schedule(sched_raw, self.season)
        settings = LeagueSettings(
            name=self.name, season=self.season,
            scoring=ScoringSettings(kind="points", points=dict(DEFAULT_POINTS_SCORING)),
            roster_slots=dict(DEFAULT_ROSTER_SLOTS), team_count=self.team_count,
            regular_season_matchups=19, playoff_teams=4, playoff_round_weeks=[2, 2],
            acquisition_limit_per_matchup=7, lineup_lock="daily",
            trade_deadline=datetime(2027, 2, 19, 17, 0, tzinfo=timezone.utc),
        )
        settings.matchup_periods = build_matchup_periods(sched, settings.regular_season_matchups,
                                                         settings.playoff_round_weeks)
        current_period = int((meta.get("currentScoringPeriod") or {}).get("id") or 1)

        # Snake draft by ADP with light positional sanity (no more than 5 centres).
        rng = random.Random(self.seed)
        ranked = sorted((p for p in players.values() if p.adp and p.adp < 200),
                        key=lambda p: p.adp + rng.uniform(-4, 4))
        roster_size = sum(v for k, v in settings.roster_slots.items() if k != "IR")
        teams = [FantasyTeam(id=i + 1, name=TEAM_NAMES[i][0], abbrev=TEAM_NAMES[i][1], owner_name=OWNERS[i])
                 for i in range(self.team_count)]
        picks = 0
        idx = 0
        while picks < roster_size * self.team_count and idx < len(ranked):
            rnd = picks // self.team_count
            order = range(self.team_count) if rnd % 2 == 0 else reversed(range(self.team_count))
            for ti in order:
                t = teams[ti]
                while idx < len(ranked):
                    cand = ranked[idx]
                    idx += 1
                    centers = sum(1 for e in t.roster if players[e.player_id].positions == ["C"])
                    if cand.positions == ["C"] and centers >= 4:
                        ranked.append(cand)  # push back for someone else
                        continue
                    t.roster.append(RosterEntry(player_id=cand.id, slot="BE"))
                    cand.fantasy_team_id = t.id
                    picks += 1
                    break
        # Put injured stars on IR when the slot exists.
        for t in teams:
            for e in t.roster:
                if players[e.player_id].injury_status in ("OUT", "INJURY_RESERVE"):
                    e.slot = "IR"
                    break

        matchups = []
        from ..strength import round_robin

        rr = round_robin([t.id for t in teams])
        for mp in range(1, settings.regular_season_matchups + 1):
            for a, b in rr[(mp - 1) % len(rr)]:
                matchups.append(Matchup(matchup_period=mp, home_team_id=a, away_team_id=b))
        current_mp = next((mp for mp, days in settings.matchup_periods.items() if current_period in days), 1)

        return LeagueSnapshot(
            settings=settings, teams=teams, players=players, matchups=matchups, schedule=sched,
            current_period=current_period, current_matchup_period=current_mp, news=news,
            source="demo", fetched_at=datetime.now(timezone.utc),
        )
