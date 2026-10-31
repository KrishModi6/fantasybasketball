"""Calibration backtest of the matchup simulator and the season simulation against real seasons.

Builds synthetic 10-team ESPN H2H points leagues (ESPN default scoring and roster, daily lineups,
19 matchup weeks, 4-team playoffs in two 2-week rounds) on top of what really happened in 2023-24
through 2025-26 (2022-23 can't be used: the out-of-sample preseason drops that season's ESPN projections,
leaving 47 players), and asks the app's own code the questions it answers in the app:

  * weekly win odds: fbb.matchup_sim (Monte Carlo) and fbb.strength.MatchupModel (Normal), against who
    really won the week
  * the 80% range (p10-p90 of each team's simulated score) against the score it really put up
  * playoff and title odds from fbb.strength.simulate_season at seven points of the season, against
    who really made the playoffs and won the title

Nothing looks ahead. Projections at each week's start come from the engine's own in-season update fed
only the games before that day, on top of out-of-sample preseason projections (scripts/backtest_recency.py
refits the ML model on earlier seasons). ESPN's injury reports aren't kept for past seasons, so a player
who has missed his team's last k games is expected to miss as many more as players who had missed k in
a row did on average in the other seasons (absence_table). xFP isn't used (no week-by-week tracking).

Leagues: managers snake-draft 13 players by preseason projection with a little noise each, keep a
lineup-able roster, put a long-term injured player on IR, and each week pick up the best free agents
when someone is hurt or clearly outplayed. Every week each manager starts the lineup with the highest
expected points and, when a starter sits, the best bench player who plays fills in: the simulator's own
lineup rule, applied to the real box scores, gives the real score.

Variance settings (CONFIGS) are the knobs in fbb/context.py, fbb/matchup_sim.py and fbb/strength.py.

Stages:
  python -m scripts.backtest_sims prepare                 # projections + outcomes -> data/cache/sims/
  python -m scripts.backtest_sims run --season 2026 --leagues 0-1 --out results/   # small local run
  python -m scripts.backtest_sims report results/          # calibration tables
The big run fans the same `run_league` out to AWS Lambda (scripts/sims_lambda.py, docs/AWS.md).

The season in progress works the same way up to its last finished week (`prepare --live`), with the app's
settings as shipped (config "app") and no playoff odds yet: scripts/daily_sims.py runs that every morning.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fbb.context as C  # noqa: E402
import fbb.matchup_sim as MS  # noqa: E402
import fbb.strength as ST  # noqa: E402
from fbb.context import AnalysisContext  # noqa: E402
from fbb.lineup import SlotModel, solve_day  # noqa: E402
from fbb.models import (  # noqa: E402
    FantasyTeam, LeagueSettings, LeagueSnapshot, Matchup, Player, ProSchedule, RosterEntry, ScoringSettings,
)
from fbb.projections import PlayerProjection  # noqa: E402
from fbb.schedule import build_matchup_periods  # noqa: E402

SEASONS = (2024, 2025, 2026)
LOG_SEASONS = (2023, 2024, 2025, 2026)  # game logs the absence table draws on
OPENING = {2023: date(2022, 10, 18), 2024: date(2023, 10, 24), 2025: date(2024, 10, 22), 2026: date(2025, 10, 21)}
TEAMS, ROUNDS, REG_WEEKS, PLAYOFF_TEAMS = 10, 13, 19, 4
CHECKPOINTS = (1, 4, 7, 10, 13, 16, 19)  # weeks at whose start playoff odds are forecast
N_SIMS = 2000  # Monte Carlo draws per matchup (the app uses 10,000 for smoother charts; 2,000 moves p by <0.012)
N_SEASON_SIMS = 4000
SEASON_CVS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)  # fbb.strength.SEASON_TEAM_CV values tried at every checkpoint
INPUTS = ROOT / "data" / "cache" / "sims"

# Settings tried. Keys are module.attribute, plus "inputs": the projections file's tag. "base" is the app
# before this study: a flat miss rate, and projections with the old team-minutes budget (its inputs were
# prepared before fbb/projections.py dropped it; tag "_noboost" is the engine without it).
_NEXT, _FLOOR, _RAMP = "context.MISS_NEXT_GAME", "context.MISS_NEXT_FLOOR", "context.MISS_RAMP_GAMES"
_PW, _TW, _ROLE = "context.PLAYER_WEEK_CV", "context.TEAM_WEEK_CV", "matchup_sim.ROLE_SHOCK_SCALE"
# Every config is a change from the app before this study, whatever the modules' defaults are now.
_DEFAULTS = {_PW: 0.0, _TW: 0.0, _NEXT: 1.0, _FLOOR: 0.0, _RAMP: 9.0, _ROLE: 1.0, "strength.SEASON_TEAM_CV": 0.0}
_RAMPED = {_NEXT: 0.30, _RAMP: 9.0}
_NEW = {"inputs": "_noboost", **_RAMPED}
_FLOORED = {"inputs": "_noboost", _NEXT: 0.15, _FLOOR: 0.04, _RAMP: 9.0}
CONFIGS: dict[str, dict] = {
    "base": {},
    "ramp": _RAMPED,
    "no boost": {"inputs": "_noboost"},
    "new": _NEW,
    "new + pw 0.10": {**_NEW, _PW: 0.10},
    "new + pw 0.20": {**_NEW, _PW: 0.20},
    "new + tw 0.04": {**_NEW, _TW: 0.04},
    "new + tw 0.08": {**_NEW, _TW: 0.08},
    "new + role x1.4": {**_NEW, _ROLE: 1.4},
    "floor": _FLOORED,
    "floor + tw 0.06": {**_FLOORED, _TW: 0.06},
    "new + tw 0.06": {**_NEW, _TW: 0.06},
    "new + tw 0.12": {**_NEW, _TW: 0.12},
    "new + pw 0.10 + tw 0.06": {**_NEW, _PW: 0.10, _TW: 0.06},
    "base + tw 0.08": {_TW: 0.08},
    "new, next game 0.45": {**_NEW, _NEXT: 0.45},
    "new, next 0.45 + tw 0.06": {**_NEW, _NEXT: 0.45, _TW: 0.06},
}
STUDY = list(CONFIGS)
# The run splits each league's configs in two so one Lambda process stays well inside 15 minutes.
GROUPS = [STUDY[:9], ["base"] + STUDY[9:]]
# The app as shipped, whatever the modules hold now: what the daily check of this season scores.
CONFIGS["app"] = {"shipped": True}
GROUPS.append(["app"])
APP_GROUP = len(GROUPS) - 1
_MODULES = {"context": C, "matchup_sim": MS, "strength": ST}
_SHIPPED = {key: getattr(_MODULES[key.split(".")[0]], key.split(".")[1]) for key in _DEFAULTS}


def apply_config(changes: dict) -> None:
    for key, default in (_SHIPPED if changes.get("shipped") else _DEFAULTS).items():
        mod, attr = key.split(".")
        setattr(_MODULES[mod], attr, changes.get(key, default))


# -- prepare (home machine: needs the engine, LightGBM and the cached ESPN game logs) ---------------------

MAX_MISSED = 20


def absence_table(season: int) -> list[np.ndarray]:
    """Further team games missed after missing k in a row (index k, 1..MAX_MISSED), every case in the
    other seasons' game logs. ESPN's injury reports and return dates aren't kept for past seasons, so this
    stands in for them: what a manager knows on average about an absence that has lasted k games."""
    from fbb.history import CACHE_DIR

    rem: dict[int, list[int]] = {k: [] for k in range(1, MAX_MISSED + 1)}
    for other in LOG_SEASONS:
        path = CACHE_DIR / f"game_logs_{other}_full.json"
        if other == season or not path.exists():
            continue
        logs = json.loads(path.read_text())
        team_days: dict[str, list[int]] = {}
        for games in logs.values():
            for g in games:
                team_days.setdefault(g["team"], []).append(g["period"])
        team_days = {t: sorted(set(d)) for t, d in team_days.items()}
        for games in logs.values():
            games = sorted(games, key=lambda g: g["period"])
            bounds = [(0, games[0])] + list(zip(games, games[1:])) + [(games[-1], None)]
            for prev, nxt in bounds:
                team = (nxt or prev)["team"]
                lo = prev["period"] if prev else 0
                hi = nxt["period"] if nxt else 10**6
                spell = sum(1 for d in team_days[team] if lo < d < hi)
                for k in range(1, min(spell, MAX_MISSED) + 1):
                    rem[k].append(spell - k)
    return [np.zeros(1)] + [np.array(rem[k] or [0]) for k in range(1, MAX_MISSED + 1)]


def opening_absences(season: int) -> np.ndarray:
    """Team games missed from the season's start by every player who missed his team's opener, in the other
    seasons' game logs. They run far longer than absences that start mid-season (about 15 games on average,
    against 3 or 4 after one game missed): offseason surgeries and injuries from camp."""
    from fbb.history import CACHE_DIR

    spells: list[int] = []
    for other in LOG_SEASONS:
        path = CACHE_DIR / f"game_logs_{other}_full.json"
        if other == season or not path.exists():
            continue
        logs = json.loads(path.read_text())
        team_days: dict[str, set[int]] = {}
        for games in logs.values():
            for g in games:
                team_days.setdefault(g["team"], set()).add(g["period"])
        for games in logs.values():
            first = min(games, key=lambda g: g["period"])
            spell = sum(1 for d in team_days[first["team"]] if d < first["period"])
            if spell:
                spells.append(spell)
    return np.array(spells or [0])


def injury_status(games_out: float) -> str:
    """The ESPN status that goes with an expected absence (it sets team minutes and IR moves)."""
    if games_out >= 6:
        return "INJURY_RESERVE"
    if games_out >= 1.5:
        return "OUT"
    return "DAY_TO_DAY" if games_out > 0 else "ACTIVE"


def prepare(season: int, tag: str = "", live: bool = False) -> Path | None:
    """One season's inputs. ``live``: the season in progress, from ESPN's games so far and its full
    schedule, through the last finished week (None before the first one ends)."""
    import fbb.projections as P
    from fbb.connectors.espn import EspnClient, parse_pro_schedule
    from fbb.history import CACHE_DIR, load_history
    from fbb.models import GameLog, SeasonLine
    from fbb.xfp import EASTERN
    from scripts.backtest_recency import closing_games, preseason, season_logs

    ds = load_history()
    if live:
        for other in LOG_SEASONS:  # the absence table and last season's closing games read these
            season_logs(other)
    logs = season_logs(season, refresh=live)
    prev_path = CACHE_DIR / f"game_logs_{season - 1}_full.json"
    closing = closing_games(json.loads(prev_path.read_text())) if prev_path.exists() else {}

    if live:
        sched = parse_pro_schedule(EspnClient().pro_schedule(season), season)
        first, dates, games_by_day = 1, sched.period_dates, sched.games
        team_days: dict[str, set[int]] = {}
        for p, teams in games_by_day.items():
            for t in teams:
                team_days.setdefault(t, set()).add(p)
    else:
        team_days = {}
        for games in logs.values():
            for g in games:
                team_days.setdefault(g["team"], set()).add(g["period"])
        first = min(p for d in team_days.values() for p in d)
        last = max(p for d in team_days.values() for p in d)
        dates = {p: OPENING[season] + timedelta(days=p - first) for p in range(1, last + 1)}
        games_by_day = {}
        for t, days in team_days.items():
            for p in days:
                games_by_day.setdefault(p, []).append(t)
        sched = ProSchedule(season=season, period_dates=dates, games=games_by_day)
    mps = build_matchup_periods(sched, REG_WEEKS, [2, 2])
    weeks = REG_WEEKS
    if live:  # weeks whose last day is over (US Eastern): their box scores are all in
        today = datetime.now(EASTERN).date()
        weeks = sum(1 for mp in range(1, REG_WEEKS + 1) if mp in mps and dates[mps[mp][-1]] < today)
        if not weeks:
            return None

    # Players with this season's games, plus those projected preseason who never played (a whole season
    # lost to injury), so drafting them can go wrong the way it really did.
    never = {pid for (pid, s) in ds.espn if s == season and pid not in logs and ds.gp.get((pid, season), 0) == 0}
    pids = set(logs) | never
    pre = preseason(season, pids)
    pids = sorted(p for p in pids if p in pre)
    absence = absence_table(season)
    opening = opening_absences(season)

    def season_team(pid: str) -> str:
        if logs.get(pid):
            return logs[pid][0]["team"]
        rows = [ln for ln in ds.lines.get(pid, []) if ln.season == season - 1]
        return rows[-1].team if rows else "FA"

    def positions(pid: str) -> list[str]:
        return ds.positions.get((pid, season)) or ds.positions.get((pid, season - 1)) or ["UT"]

    proj_out: dict[int, dict] = {}
    for mp, days in sorted(mps.items()):
        if live and mp > weeks:
            break
        start = days[0]
        players = {}
        out: dict[str, float] = {}
        status: dict[str, str] = {}
        for pid in pids:
            seen = [g for g in logs.get(pid, []) if g["period"] < start]
            team = seen[-1]["team"] if seen else season_team(pid)
            played = {g["period"] for g in seen}
            missed = 0
            for p in sorted((d for d in team_days.get(team, ()) if d < start), reverse=True):
                if p in played:
                    break
                missed += 1
            # The engine sits a player for his first games_out games. A long absence is its whole expected
            # length; a short one, the games he's expected to miss this week (half of one-game absences end
            # with the next game, but the few long injuries would otherwise bench everyone who rested one).
            rem = absence[min(missed, MAX_MISSED)]
            week_games = sum(1 for d in team_days.get(team, ()) if start <= d <= days[-1])
            status[pid] = injury_status(float(rem.mean())) if missed else "ACTIVE"
            out[pid] = 0.0 if not missed else (float(rem.mean()) if status[pid] == "INJURY_RESERVE"
                                               else float(np.minimum(rem, week_games).mean()))
            # Before his team's opener nobody has missed a game yet, but the injury report already says who will.
            # A player who missed the opener stands for one it listed: out from the start, as long as such
            # absences lasted in the other seasons. Only this week's games count, since nothing reads the
            # absence's length before week 2's moves. Without this, week 1's players out from the start were
            # simulated to play 2,740 games in synthetic leagues and played 263: 4% too many starts.
            tdays = sorted(team_days.get(team, ()))
            if tdays and tdays[0] >= start and tdays[0] not in {g["period"] for g in logs.get(pid, [])}:
                status[pid] = injury_status(float(opening.mean()))
                out[pid] = float(np.minimum(opening, week_games).mean())
            stats: dict[str, float] = {}
            for g in seen:
                for k, v in g["stats"].items():
                    stats[k] = stats.get(k, 0.0) + v
            cur = SeasonLine(season=season, team=team, gp=len(seen), minutes=sum(g["minutes"] for g in seen),
                             stats=stats) if seen else None
            players[pid] = Player(
                id=pid, name=ds.names.get(pid, pid), pro_team=team, positions=positions(pid),
                injury_status=status[pid],
                history=[h for h in ds.lines.get(pid, []) if h.season < season], current=cur,
                recent_games=[GameLog(**g) for g in closing.get(pid, []) + seen],
            )
        snap = LeagueSnapshot(settings=LeagueSettings(name="backtest", season=season, matchup_periods=mps),
                              teams=[], players=players, schedule=sched, current_period=start)
        engine = P.ProjectionEngine(snap)
        engine._preseason = lambda _players: pre  # out-of-sample preseason, nothing from this season
        proj = engine.project_all()
        for pid, pr in proj.items():  # instead of the status's default games missed
            pr.games_out = out[pid]
        proj_out[mp] = {pid: {
            "team": players[pid].pro_team, "status": players[pid].injury_status, "mpg": pr.mpg,
            "availability": pr.availability, "games_out": pr.games_out, "fp_pg": pr.fp_pg, "fp_sd": pr.fp_sd,
            "gp": pr.games_played, "missed": pr.games_missed, "back_from": pr.back_from, "back": pr.games_back,
            "per_game": {k: round(v, 4) for k, v in pr.per_game.items()},
        } for pid, pr in proj.items()}
        print(f"  {season} week {mp}: projected {len(proj)} players", flush=True)

    fp = ScoringSettings().fantasy_points
    out = {
        "season": season, "first": first, "live": live, "weeks": weeks,
        "dates": {p: d.isoformat() for p, d in dates.items()},
        "games": {p: sorted(t) for p, t in games_by_day.items()},
        "matchup_periods": mps,
        "players": {pid: {"name": ds.names.get(pid, pid), "positions": positions(pid)} for pid in pids},
        "proj": proj_out,
        # Real fantasy points by day, for games actually played.
        "actual": {pid: {g["period"]: round(fp(g["stats"]), 2) for g in logs.get(pid, []) if g["minutes"] > 0}
                   for pid in pids},
    }
    INPUTS.mkdir(parents=True, exist_ok=True)
    path = INPUTS / f"inputs_{season}{tag}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(out, f)
    return path


# -- one league-season (runs anywhere: numpy + pydantic + fbb) -----------------------------------------

class Season:
    """A season's inputs turned into the engine's objects once, shared by every league."""

    def __init__(self, raw: dict):
        self.season = raw["season"]
        self.live = raw.get("live", False)  # still being played: no outcome, no playoff odds to check
        self.weeks = raw.get("weeks", REG_WEEKS)  # matchup weeks with every game played
        self.mps = {int(k): v for k, v in raw["matchup_periods"].items()}
        self.schedule = ProSchedule(season=self.season,
                                    period_dates={int(p): date.fromisoformat(d) for p, d in raw["dates"].items()},
                                    games={int(p): t for p, t in raw["games"].items()})
        self.settings = LeagueSettings(name="backtest", season=self.season, team_count=TEAMS,
                                       regular_season_matchups=REG_WEEKS, playoff_teams=PLAYOFF_TEAMS,
                                       matchup_periods=self.mps)
        self.info = raw["players"]
        self.actual = {pid: {int(p): v for p, v in d.items()} for pid, d in raw["actual"].items()}
        self.proj: dict[int, dict[str, PlayerProjection]] = {}
        self.players: dict[int, dict[str, Player]] = {}
        self.status: dict[int, dict[str, str]] = {}
        for mp, rows in raw["proj"].items():
            mp = int(mp)
            self.proj[mp] = {pid: PlayerProjection(
                player_id=pid, name=self.info[pid]["name"], pro_team=r["team"], positions=self.info[pid]["positions"],
                mpg=r["mpg"], availability=r["availability"], games_out=r["games_out"], per_game=r["per_game"],
                fp_pg=r["fp_pg"], fp_sd=r["fp_sd"], preseason_fp_pg=r["fp_pg"], season_fp_pg=None,
                # Inputs prepared before the opening and return dips have no games played or absences: no dips.
                recent_fp_pg=None, games_played=r.get("gp", 10**6), games_missed=r.get("missed", 0),
                back_from=r.get("back_from", 0), games_back=r.get("back", 0)) for pid, r in rows.items()}
            self.players[mp] = {pid: Player(id=pid, name=self.info[pid]["name"], pro_team=r["team"],
                                            positions=self.info[pid]["positions"], injury_status=r["status"])
                                for pid, r in rows.items()}
            self.status[mp] = {pid: r["status"] for pid, r in rows.items()}
        self.slots = SlotModel(self.settings.roster_slots)
        self.mask = {pid: self.slots.mask_for([], v["positions"]) for pid, v in self.info.items()}

    def snapshot(self, mp: int, rosters: dict[int, list[str]], ir: dict[int, str | None], records: dict,
                 schedule: list[tuple[int, int, int]], results: dict[tuple[int, int], str]) -> LeagueSnapshot:
        teams = []
        for tid in range(1, TEAMS + 1):
            entries = [RosterEntry(player_id=pid, slot="BE") for pid in rosters[tid]]
            if ir[tid]:
                entries.append(RosterEntry(player_id=ir[tid], slot="IR"))
            w, l, t = records[tid]
            teams.append(FantasyTeam(id=tid, name=f"Team {tid}", abbrev=f"T{tid}", roster=entries,
                                     wins=w, losses=l, ties=t))
        matchups = [Matchup(matchup_period=m, home_team_id=a, away_team_id=b, winner=results.get((m, a), "UNDECIDED"))
                    for m, a, b in schedule]
        return LeagueSnapshot(settings=self.settings, teams=teams, players=self.players[mp], matchups=matchups,
                              schedule=self.schedule, current_period=self.mps[mp][0], current_matchup_period=mp)


def _value(season: Season, mp: int, pid: str, horizon: int = 14) -> float:
    """What a manager thinks a player is worth now: expected points over the next two weeks."""
    pr = season.proj[mp][pid]
    start = season.mps[mp][0]
    days = [p for p in range(start, start + horizon) if pr.pro_team in season.schedule.games.get(p, ())]
    games = max(0.0, len(days) - pr.games_out) * pr.availability
    return pr.fp_pg * games


def _fills_lineup(season: Season, roster: list[str], extra: int) -> bool:
    """Whether a roster (plus ``extra`` picks still to make) can fill every starting slot."""
    assigned = solve_day([(i, 1.0, season.mask[pid]) for i, pid in enumerate(roster)], season.slots.n)
    return season.slots.n - len(assigned) <= extra


def draft(season: Season, rng: np.random.Generator) -> dict[int, list[str]]:
    base = {pid: pr.fp_pg * pr.availability for pid, pr in season.proj[1].items()}
    rosters = {tid: [] for tid in range(1, TEAMS + 1)}
    taste = {tid: {pid: v * math.exp(0.10 * rng.standard_normal()) for pid, v in base.items()} for tid in rosters}
    taken: set[str] = set()
    order = list(rng.permutation(range(1, TEAMS + 1)))
    for rnd in range(ROUNDS):
        for tid in (order if rnd % 2 == 0 else order[::-1]):
            left = ROUNDS - rnd - 1
            for pid in sorted(base, key=lambda p: -taste[tid][p]):
                if pid not in taken and _fills_lineup(season, rosters[tid] + [pid], left):
                    rosters[tid].append(pid)
                    taken.add(pid)
                    break
    return rosters


def manage(season: Season, mp: int, rosters, ir, rng: np.random.Generator) -> None:
    """Weekly moves: long-term injured players to IR, returning ones back, and up to two pickups each
    (injury replacements first, then free agents clearly better than the worst rostered player)."""
    status = season.status[mp]
    taken = {pid for r in rosters.values() for pid in r} | {p for p in ir.values() if p}
    free = sorted((p for p in season.proj[mp] if p not in taken), key=lambda p: -_value(season, mp, p))
    for tid in rng.permutation(range(1, TEAMS + 1)):
        tid = int(tid)
        roster = rosters[tid]
        if ir[tid] and status.get(ir[tid]) != "INJURY_RESERVE":  # back from injury
            worst = min(roster, key=lambda p: _value(season, mp, p))
            back, ir[tid] = ir[tid], None
            if _value(season, mp, back) > _value(season, mp, worst):
                roster.remove(worst)
                roster.append(back)
                back = worst
            free.append(back)
            free.sort(key=lambda p: -_value(season, mp, p))
        for pid in sorted(roster, key=lambda p: _value(season, mp, p)):
            if status.get(pid) == "INJURY_RESERVE" and ir[tid] is None:
                roster.remove(pid)
                ir[tid] = pid
        adds = 0
        while len(roster) < ROUNDS and free:
            pick = next((p for p in free if _fills_lineup(season, roster + [p], ROUNDS - len(roster) - 1)), None)
            if pick is None:
                break
            free.remove(pick)
            roster.append(pick)
            adds += 1
        while adds < 2 and free:
            worst = min(roster, key=lambda p: _value(season, mp, p))
            rest = [p for p in roster if p != worst]
            pick = next((p for p in free if _fills_lineup(season, rest + [p], 0)), None)
            if pick is None or _value(season, mp, pick) < 1.15 * _value(season, mp, worst) + 5:
                break
            free.remove(pick)
            roster.remove(worst)
            roster.append(pick)
            free.append(worst)
            free.sort(key=lambda p: -_value(season, mp, p))
            adds += 1


def real_score(season: Season, players, plans) -> tuple[float, float]:
    """The score a lineup plan really produced: the simulator's lineup rule on the real box scores."""
    total = starts = 0.0
    for day in plans:
        plays = [day.period in season.actual.get(players[i].pid, {}) for i in day.cands]
        use = [False] * len(day.cands)
        for pos, _ in day.starters:
            use[pos] = plays[pos]
        for pos, j in day.starters:
            if plays[pos]:
                continue
            for b in day.fill_order[j]:
                if plays[b] and not use[b]:
                    use[b] = True
                    break
        for pos, i in enumerate(day.cands):
            if use[pos]:
                total += season.actual[players[i].pid][day.period]
                starts += 1
    return total, starts


def round_robin_schedule() -> list[tuple[int, int, int]]:
    rr = ST.round_robin(list(range(1, TEAMS + 1)))
    return [(mp, a, b) for mp in range(1, REG_WEEKS + 1) for a, b in rr[(mp - 1) % len(rr)]]


def run_league(season: Season, league: int, configs: list[str] | None = None) -> dict:
    """One synthetic league through a whole season, or a live one through its last finished week.
    Returns forecast rows and the real outcome. The first config's lineups decide the real results."""
    configs = list(configs or STUDY)
    rng = np.random.default_rng([season.season, league])
    rosters = draft(season, rng)
    ir: dict[int, str | None] = {tid: None for tid in rosters}
    records = {tid: [0, 0, 0] for tid in rosters}
    points_for = {tid: 0.0 for tid in rosters}
    schedule = round_robin_schedule()
    results: dict[tuple[int, int], str] = {}
    weekly, odds = [], []
    w_fp = MS.fp_weights(ScoringSettings())
    for mp in range(1, season.weeks + 1):
        if mp > 1:
            manage(season, mp, rosters, ir, rng)
        snap = season.snapshot(mp, rosters, ir, records, schedule, results)
        games = [(a, b) for m, a, b in schedule if m == mp]
        real = {}
        for cfg in configs:
            apply_config(CONFIGS[cfg])
            # Managers draft and move by the base projections, so every config forecasts the same matchups.
            ctx = AnalysisContext(snap, load_season(season.season, CONFIGS[cfg].get("inputs", "")).proj[mp])
            days = ctx.matchup_days(mp)
            for a, b in games:
                sim = MS.MatchupSimulator(ctx, seed=hash((season.season, league, mp, a)) % 2**32)
                side = []
                for tid in (a, b):
                    players, plans = sim._plan(ctx.team_roster(tid), days, set())
                    totals, _, starts, _, _ = sim._simulate_team(players, plans, N_SIMS)
                    score = totals @ w_fp
                    actual, real_starts = real_score(season, players, plans)
                    win = ctx.project_window(ctx.team_roster(tid), days)
                    side.append((tid, score, actual, float(starts.sum()), real_starts, win))
                (ta, sa, xa, ea, ra, wa), (tb, sb, xb, eb, rb, wb) = side
                p_norm = ST.MatchupModel(ctx, {ta: wa, tb: wb}).p_win(ta, tb, mp)
                margin = sa - sb
                row = {"cfg": cfg, "mp": mp, "a": ta, "b": tb, "p_mc": float((sa > sb).mean() + 0.5 * (sa == sb).mean()),
                       "p_norm": p_norm, "real_a": xa, "real_b": xb,
                       "m_p10": float(np.percentile(margin, 10)), "m_p90": float(np.percentile(margin, 90))}
                for tag, s, x, e, r, w in (("a", sa, xa, ea, ra, wa), ("b", sb, xb, eb, rb, wb)):
                    q = np.percentile(s, [10, 25, 50, 75, 90])
                    row.update({f"mean_{tag}": float(s.mean()), f"sd_{tag}": float(s.std()),
                                f"q_{tag}": [round(float(v), 2) for v in q],
                                f"pit_{tag}": float((s < x).mean() + 0.5 * (s == x).mean()),
                                f"norm_mean_{tag}": w.by_mp.get(mp, [0, 0])[0], f"norm_sd_{tag}": math.sqrt(w.by_mp.get(mp, [0, 0])[1]),
                                f"starts_{tag}": e, f"real_starts_{tag}": r})
                weekly.append(row)
                if cfg == configs[0]:
                    real[(a, b)] = (xa, xb)
            if mp in CHECKPOINTS and not season.live:
                odds.extend(_season_odds(season, ctx, cfg, mp, league))
        apply_config({})
        for (a, b), (xa, xb) in real.items():
            res = "HOME" if xa > xb else ("AWAY" if xb > xa else "TIE")
            results[(mp, a)] = res
            points_for[a] += xa
            points_for[b] += xb
            for tid, won in ((a, xa > xb), (b, xb > xa)):
                records[tid][0 if won else (2 if xa == xb else 1)] += 1
    if season.live:
        return {"season": season.season, "league": league, "weekly": weekly, "odds": odds, "outcome": None}
    # Playoffs: 1 v 4 and 2 v 3 over weeks 20, then the final over week 21, by real scores.
    seeds = sorted(records, key=lambda t: (-(records[t][0] + 0.5 * records[t][2]), -points_for[t]))[:PLAYOFF_TEAMS]
    champion = None
    finalists = []
    for mp, pairs in ((REG_WEEKS + 1, [(seeds[0], seeds[3]), (seeds[1], seeds[2])]), (REG_WEEKS + 2, None)):
        manage(season, mp, rosters, ir, rng)
        snap = season.snapshot(mp, rosters, ir, records, schedule, results)
        ctx = AnalysisContext(snap, season.proj[mp])
        sim = MS.MatchupSimulator(ctx, seed=0)
        pairs = pairs or [tuple(finalists)]
        winners = []
        for a, b in pairs:
            xa = real_score(season, *sim._plan(ctx.team_roster(a), ctx.matchup_days(mp), set()))[0]
            xb = real_score(season, *sim._plan(ctx.team_roster(b), ctx.matchup_days(mp), set()))[0]
            winners.append(a if xa >= xb else b)
        finalists = winners
        champion = winners[0] if len(winners) == 1 else None
    return {"season": season.season, "league": league, "weekly": weekly, "odds": odds,
            "outcome": {"records": records, "points_for": points_for, "playoffs": seeds, "champion": champion}}


def _season_odds(season: Season, ctx: AnalysisContext, cfg: str, mp: int, league: int) -> list[dict]:
    results = ST.team_windows(ctx)
    mm = ST.MatchupModel(ctx, results)
    future = [m for m in ctx.remaining_mps(include_playoffs=False) if m > mp] or [mp]
    ppw = {tid: float(np.mean([results[tid].by_mp.get(m, [0, 0])[0] for m in future])) for tid in results}
    rows = []
    for scv in SEASON_CVS:
        ST.SEASON_TEAM_CV = scv
        sim = ST.simulate_season(ctx, mm, n=N_SEASON_SIMS, seed=hash((season.season, league, mp)) % 2**32, ppw=ppw)
        for tid, r in sim.items():
            rows.append({"cfg": cfg, "season_cv": scv, "mp": mp, "team": tid, "playoff": r["playoff_odds"],
                         "title": r["title_odds"], "proj_wins": r["proj_wins"]})
    ST.SEASON_TEAM_CV = 0.0
    return rows


_SEASON_CACHE: dict[tuple[int, str], Season] = {}


def load_season(season: int, tag: str = "") -> Season:
    if (season, tag) not in _SEASON_CACHE:
        with gzip.open(INPUTS / f"inputs_{season}{tag}.json.gz", "rt", encoding="utf-8") as f:
            _SEASON_CACHE[(season, tag)] = Season(json.load(f))
    return _SEASON_CACHE[(season, tag)]


def input_tags(configs: list[str]) -> set[str]:
    """The projections files a set of configs reads (besides the base one)."""
    return {CONFIGS[c].get("inputs", "") for c in configs} | {""}


# -- report -------------------------------------------------------------------------------------------

def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _calibration_slope(p: np.ndarray, y: np.ndarray) -> float:
    """Slope of a logistic regression of the outcome on logit(p): 1 is calibrated, below 1 overconfident."""
    x = _logit(p)
    if len(x) < 20 or x.std() < 1e-6:
        return float("nan")
    a, b = 0.0, 1.0
    for _ in range(50):  # Newton
        z = np.clip(a + b * x, -30, 30)
        q = 1 / (1 + np.exp(-z))
        w = q * (1 - q) + 1e-9
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        h = np.array([[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]]) + 1e-9 * np.eye(2)
        step = np.clip(np.linalg.solve(h, g), -2, 2)
        a, b = a + step[0], b + step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(b)


def _bins(p: np.ndarray, y: np.ndarray, edges=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001)) -> list[tuple]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            out.append((lo, hi, int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return out


def _bootstrap(stat, groups: np.ndarray, rng, reps: int = 400) -> tuple[float, float]:
    """95% interval of ``stat(index)`` resampling whole groups (season-weeks share real box scores)."""
    uniq = np.unique(groups)
    idx = {g: np.nonzero(groups == g)[0] for g in uniq}
    vals = []
    for _ in range(reps):
        pick = rng.choice(uniq, size=len(uniq))
        vals.append(stat(np.concatenate([idx[g] for g in pick])))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def report(paths: list[Path], out: Path | None = None) -> dict:
    leagues = []
    for path in paths:
        files = sorted(path.glob("*.json.gz")) if path.is_dir() else [path]
        for f in files:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            leagues.extend(data if isinstance(data, list) else [data])
    summary = summarize(leagues)
    if out:
        out.write_text(json.dumps(summary, indent=1))
    return summary


def summarize(leagues: list[dict]) -> dict:
    rng = np.random.default_rng(0)
    summary: dict = {"leagues": len({(lg["season"], lg["league"]) for lg in leagues}), "seasons": sorted({lg["season"] for lg in leagues}), "weekly": {}, "season": {}}

    # Weekly odds and ranges.
    rows = [dict(r, season=lg["season"], league=lg["league"]) for lg in leagues for r in lg["weekly"]]
    cfgs = list(dict.fromkeys(r["cfg"] for r in rows))
    # Compare every config on the matchups all of them forecast, in the same order.
    by_cfg = {cfg: {(r["season"], r["league"], r["mp"], r["a"]): r for r in rows if r["cfg"] == cfg} for cfg in cfgs}
    common = sorted(set.intersection(*(set(d) for d in by_cfg.values())))
    base_err = None
    for cfg in cfgs:
        rs = [by_cfg[cfg][k] for k in common]
        y = np.array([1.0 if r["real_a"] > r["real_b"] else (0.5 if r["real_a"] == r["real_b"] else 0.0) for r in rs])
        grp = np.array([r["season"] * 100 + r["mp"] for r in rs])
        res = {"matchups": len(rs)}
        for key in ("p_mc", "p_norm"):
            p = np.array([r[key] for r in rs])
            brier = (p - y) ** 2
            ll = -(y * np.log(np.clip(p, 1e-4, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-4, 1)))
            res[key] = {"brier": float(brier.mean()), "logloss": float(ll.mean()),
                        "slope": _calibration_slope(p, y),
                        "slope_ci": _bootstrap(lambda i: _calibration_slope(p[i], y[i]), grp, rng, 200),
                        "bins": _bins(p, y)}
            res[key]["ece"] = float(sum(n * abs(pm - ym) for _, _, n, pm, ym in res[key]["bins"]) / len(p))
            if cfg == "base":
                base_err = base_err or {}
                base_err[key] = (brier, ll)
            elif base_err:
                b_brier, b_ll = base_err[key]
                d = ll - b_ll
                res[key]["logloss_vs_base"] = float(d.mean())
                res[key]["logloss_vs_base_ci"] = _bootstrap(lambda i: d[i].mean(), grp, rng)
        # Each team's score against its simulated distribution.
        pit = np.array([r[f"pit_{t}"] for r in rs for t in "ab"])
        grp2 = np.repeat(grp, 2)
        q = np.array([r[f"q_{t}"] for r in rs for t in "ab"])
        real = np.array([r[f"real_{t}"] for r in rs for t in "ab"])
        inside80 = (real >= q[:, 0]) & (real <= q[:, 4])
        res["range80"] = {"coverage": float(inside80.mean()),
                          "ci": _bootstrap(lambda i: inside80[i].mean(), grp2, rng),
                          "below_p10": float((real < q[:, 0]).mean()), "above_p90": float((real > q[:, 4]).mean())}
        res["range50"] = float(((real >= q[:, 1]) & (real <= q[:, 3])).mean())
        res["pit_deciles"] = np.histogram(pit, np.linspace(0, 1, 11))[0].tolist()
        res["pit_sd"] = float(pit.std())  # 0.289 when calibrated
        mean = np.array([r[f"mean_{t}"] for r in rs for t in "ab"])
        res["bias_pts"] = float((real - mean).mean())
        res["bias_pct"] = float((real - mean).mean() / real.mean())
        res["starts"] = {"sim": float(np.mean([r[f"starts_{t}"] for r in rs for t in "ab"])),
                         "real": float(np.mean([r[f"real_starts_{t}"] for r in rs for t in "ab"]))}
        m_in = np.array([(r["real_a"] - r["real_b"] >= r["m_p10"]) and (r["real_a"] - r["real_b"] <= r["m_p90"]) for r in rs])
        res["margin80"] = float(m_in.mean())
        nm = np.array([r[f"norm_mean_{t}"] for r in rs for t in "ab"])
        ns = np.array([r[f"norm_sd_{t}"] for r in rs for t in "ab"])
        res["norm_range80"] = float((np.abs(real - nm) <= 1.2816 * ns).mean())
        # Week by week, for the season in progress.
        p_mc = np.array([r["p_mc"] for r in rs])
        mps = np.array([r["mp"] for r in rs])
        called = (p_mc != 0.5) & (y != 0.5)
        res["by_week"] = {}
        for mp in sorted(set(mps.tolist())):
            m = mps == mp
            res["by_week"][int(mp)] = {
                "matchups": int(m.sum()), "brier": float(((p_mc[m] - y[m]) ** 2).mean()),
                "favourites_won": float(((p_mc > 0.5) == (y > 0.5))[m & called].mean()) if (m & called).any() else None,
                "range80": float(inside80[np.repeat(m, 2)].mean())}
        summary["weekly"][cfg] = res

    # Playoff and title odds.
    out_by = {(lg["season"], lg["league"]): lg["outcome"] for lg in leagues}
    odds = list({(r["cfg"], r["season_cv"], lg["season"], lg["league"], r["mp"], r["team"]):
                 dict(r, season=lg["season"], league=lg["league"]) for lg in leagues for r in lg["odds"]}.values())
    keys = list(dict.fromkeys((r["cfg"], r["season_cv"]) for r in odds))
    for cfg, scv in keys:
        rs = [r for r in odds if r["cfg"] == cfg and r["season_cv"] == scv]
        made = np.array([1.0 if r["team"] in out_by[(r["season"], r["league"])]["playoffs"] else 0.0 for r in rs])
        champ = np.array([1.0 if r["team"] == out_by[(r["season"], r["league"])]["champion"] else 0.0 for r in rs])
        p = np.array([r["playoff"] for r in rs])
        t = np.array([r["title"] for r in rs])
        grp = np.array([r["season"] * 1000 + r["league"] for r in rs])
        by_cp = {}
        for mp in sorted({r["mp"] for r in rs}):
            m = np.array([r["mp"] == mp for r in rs])
            by_cp[mp] = {"brier": float(((p[m] - made[m]) ** 2).mean()), "slope": _calibration_slope(p[m], made[m])}
        d = (p - made) ** 2
        summary["season"][f"{cfg} | season_cv {scv}"] = {
            "forecasts": len(rs), "playoff_brier": float(d.mean()),
            "playoff_brier_ci": _bootstrap(lambda i: d[i].mean(), grp, rng),
            "playoff_slope": _calibration_slope(p, made),
            "playoff_slope_ci": _bootstrap(lambda i: _calibration_slope(p[i], made[i]), grp, rng, 200),
            "playoff_bins": _bins(p, made), "title_brier": float(((t - champ) ** 2).mean()),
            "title_slope": _calibration_slope(t, champ), "title_bins": _bins(t, champ,
                                                                         (0, .05, .1, .2, .3, .5, 1.0001)),
            "by_checkpoint": by_cp,
        }
    return summary


def print_report(s: dict) -> None:
    print(f"\n{s['leagues']} league-seasons ({', '.join(map(str, s['seasons']))})")
    print(f"\nWeekly matchups (n per config = {next(iter(s['weekly'].values()))['matchups']})")
    print(f"{'config':<26}{'MC brier':>9}{'logloss':>9}{'slope':>7}{'  slope 95%':<14}{'ECE':>6}{'d logloss vs base':>28}"
          f"{'80% range':>10}{'<p10':>6}{'>p90':>6}{'50%':>6}{'PIT sd':>7}{'bias':>7}{'bias%':>7}{'starts':>13}"
          f"{'margin 80%':>11}{'N slope':>8}{'N 80%':>7}")
    for cfg, r in s["weekly"].items():
        mc = r["p_mc"]
        dv = mc.get("logloss_vs_base")
        dci = mc.get("logloss_vs_base_ci")
        dtxt = f"{dv:+.4f} [{dci[0]:+.4f},{dci[1]:+.4f}]" if dv is not None else ""
        st = f"{r['starts']['sim']:.1f}/{r['starts']['real']:.1f}"
        print(f"{cfg:<26}{mc['brier']:>9.4f}{mc['logloss']:>9.4f}{mc['slope']:>7.2f}  [{mc['slope_ci'][0]:.2f},{mc['slope_ci'][1]:.2f}]  "
              f"{mc['ece']:>6.3f}{dtxt:>28}{r['range80']['coverage']:>10.3f}{r['range80']['below_p10']:>6.3f}"
              f"{r['range80']['above_p90']:>6.3f}{r['range50']:>6.3f}{r['pit_sd']:>7.3f}{r['bias_pts']:>7.1f}"
              f"{r['bias_pct']:>7.1%}{st:>13}{r['margin80']:>11.3f}{r['p_norm']['slope']:>8.2f}{r['norm_range80']:>7.3f}")
    print("\nPlayoff odds")
    print(f"{'config':<44}{'n':>7}{'brier':>8}{'  95%':<18}{'slope':>7}{'  slope 95%':<14}{'title brier':>12}{'title slope':>12}")
    for k, r in s["season"].items():
        print(f"{k:<44}{r['forecasts']:>7}{r['playoff_brier']:>8.4f}  [{r['playoff_brier_ci'][0]:.4f},{r['playoff_brier_ci'][1]:.4f}] "
              f"{r['playoff_slope']:>7.2f}  [{r['playoff_slope_ci'][0]:.2f},{r['playoff_slope_ci'][1]:.2f}]"
              f"{r['title_brier']:>12.4f}{r['title_slope']:>12.2f}")


# -- CLI ----------------------------------------------------------------------------------------------

def _range(spec: str) -> list[int]:
    lo, _, hi = spec.partition("-")
    return list(range(int(lo), int(hi or lo) + 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--season", type=int, action="append")
    p.add_argument("--tag", default="", help="suffix for the inputs file, e.g. _teamfix")
    r = sub.add_parser("run")
    r.add_argument("--season", type=int, required=True)
    r.add_argument("--leagues", default="0")
    r.add_argument("--configs", default="")
    r.add_argument("--group", type=int, help="run the configs of GROUPS[group]")
    r.add_argument("--out", type=Path, required=True)
    rep = sub.add_parser("report")
    rep.add_argument("paths", type=Path, nargs="+")
    rep.add_argument("--json", type=Path)
    args = ap.parse_args()
    if args.cmd == "prepare":
        for season in args.season or SEASONS:
            t = time.time()
            print(f"wrote {prepare(season, args.tag)} in {time.time() - t:.0f}s", flush=True)
    elif args.cmd == "run":
        season = load_season(args.season)
        args.out.mkdir(parents=True, exist_ok=True)
        for league in _range(args.leagues):
            t = time.time()
            configs = GROUPS[args.group] if args.group is not None else [c for c in args.configs.split(",") if c]
            res = run_league(season, league, configs or None)
            name = f"{args.season}_{league}" + (f"_g{args.group}" if args.group is not None else "")
            with gzip.open(args.out / f"{name}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(res, f)
            print(f"{args.season} league {league}: {time.time() - t:.0f}s", flush=True)
    else:
        print_report(report(args.paths, args.json))


if __name__ == "__main__":
    main()
