"""Synthetic league factory for fast, offline engine tests."""

from __future__ import annotations

import random
from datetime import date, timedelta

from fbb.models import (
    FantasyTeam,
    LeagueSettings,
    LeagueSnapshot,
    Matchup,
    Player,
    ProSchedule,
    RosterEntry,
    ScoringSettings,
    SeasonLine,
)
from fbb.schedule import build_matchup_periods

NBA = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def make_line(season: int, gp: float, mpg: float, scale: float, team: str = "AAA") -> SeasonLine:
    m = gp * mpg
    per36 = {"PTS": 18, "REB": 7, "AST": 4, "STL": 1.1, "BLK": 0.7, "TO": 2.0, "FGM": 7, "FGA": 15, "FTM": 3,
             "FTA": 3.8, "3PM": 1.6, "3PA": 4.4, "DD": 0.15, "TD": 0.01, "PF": 2.4}
    stats = {k: v * scale * m / 36 for k, v in per36.items()}
    # keep shooting consistent with points: PTS = 2*FGM + 3PM + FTM
    stats["PTS"] = 2 * stats["FGM"] + stats["3PM"] + stats["FTM"]
    return SeasonLine(season=season, team=team, gp=gp, minutes=m, stats=stats)


def make_snapshot(n_teams: int = 4, roster: int = 8, seed: int = 3, injured: dict | None = None) -> LeagueSnapshot:
    rng = random.Random(seed)
    start = date(2026, 10, 19)  # a Monday
    games = {}
    dates = {}
    for p in range(1, 120):
        dates[p] = start + timedelta(days=p - 1)
        # each pro team plays roughly every other day
        games[p] = sorted(t for i, t in enumerate(NBA) if (p + i) % 2 == 0 or (p + i) % 7 == 0)
    sched = ProSchedule(season=2027, period_dates=dates, games=games)
    settings = LeagueSettings(
        name="Test", season=2027, scoring=ScoringSettings(),
        roster_slots={"G": 1, "F": 1, "C": 1, "UT": 2, "BE": 3, "IR": 1}, team_count=n_teams,
        regular_season_matchups=12, playoff_teams=2, playoff_round_weeks=[1],
    )
    settings.matchup_periods = build_matchup_periods(sched, 12, [1])
    players = {}
    pos_cycle = [["PG", "SG"], ["SF"], ["PF", "C"], ["C"], ["SG", "SF"], ["PG"], ["PF"], ["SF", "PF"]]
    pid = 0
    teams = []
    for t in range(1, n_teams + 1):
        ft = FantasyTeam(id=t, name=f"Team {t}", abbrev=f"T{t}")
        for i in range(roster):
            pid += 1
            scale = rng.uniform(0.6, 1.5) * (1.15 if t == 1 else 1.0)  # team 1 is stronger
            nba = NBA[pid % len(NBA)]
            hist = [make_line(s, rng.uniform(55, 80), rng.uniform(22, 34), scale, nba) for s in (2024, 2025, 2026)]
            p = Player(id=str(pid), name=f"Player {pid}", pro_team=nba, positions=pos_cycle[i % len(pos_cycle)],
                       age=rng.uniform(21, 34), history=hist, fantasy_team_id=t)
            players[p.id] = p
            ft.roster.append(RosterEntry(player_id=p.id))
        teams.append(ft)
    for i in range(12):  # free agents
        pid += 1
        nba = NBA[pid % len(NBA)]
        hist = [make_line(s, 60, 20, rng.uniform(0.5, 0.9), nba) for s in (2025, 2026)]
        players[str(pid)] = Player(id=str(pid), name=f"Free {pid}", pro_team=nba, positions=["SF"], age=26,
                                   history=hist)
    for k, status in (injured or {}).items():
        players[k].injury_status = status
    from fbb.strength import round_robin

    rr = round_robin([t.id for t in teams])
    matchups = [Matchup(matchup_period=mp, home_team_id=a, away_team_id=b)
                for mp in range(1, 13) for a, b in rr[(mp - 1) % len(rr)]]
    return LeagueSnapshot(settings=settings, teams=teams, players=players, matchups=matchups, schedule=sched,
                          current_period=1, current_matchup_period=1)
