"""Does following Courtside's advice win? The add/drop study, on scripts/backtest_sims.py's synthetic leagues.

Each league is played once per arm. Every arm drafts the same teams. Nine teams always manage like people
in a real league (RIVALS below), and team 1, whose draft slot is random, manages four ways:
  * "averages": the same as its rivals, the control.
  * "courtside": the app's moves. Its top add/drop move (fbb.improve.add_drop_moves) while one gains
    rest-of-season points, and an open roster spot goes to the free agent who adds the most by the same
    measure (fbb.context.project_window).
  * "projections": its rivals' rule by Courtside's projections instead of their averages, so the
    projections alone, without the app's add/drop logic.
  * "recent": its rivals' rule by points per game over the last 15 days (ESPN's other column).
Each makes at most two pickups a week (more only to fill open roster spots) and handles IR the same way,
so the arms differ only in whom they pick up and drop. Every team starts the lineup with the most
expected points every day, and the real box scores decide every week and the playoffs.

RIVALS judge pickups by points per game so far (the preseason projection until a player has played) times
his team's games in the next two weeks, less the games he's listed out: what ESPN shows. They let go of
whoever is lowest on their roster in points per game, so a hurt star stays, until he has missed GIVE_UP
team games in a row. backtest_sims.manage's rule can't stand in for them here: it judges both by the next
two weeks and by Courtside's projections, so its managers dropped 26-49 players of 35+ points per game a
league-season, 82-98% of them while hurt (Durant, out 1.5 games), and never chased a hot start. That fills
the waiver wire with stars and leaves the breakouts to whoever looks, which flatters anyone who manages
differently. It doesn't matter for what that study measures, the odds of matchups already set.

The arms play the same leagues, so comparing team 1's results league by league (wins, points, playoff
spots, titles) takes the draft's luck out of the differences. The intervals resample leagues, which share
each season's real games: they say how sure the number is for these three seasons, not for the next one.
The season-by-season lines show how much it moves.

  python -m scripts.backtest_advice run --season 2026 --leagues 0-1 --out results/
  python -m scripts.backtest_advice report results/
On AWS Lambda: scripts/sims_lambda.py with {"study": "advice"} (docs/AWS.md, section 4).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fbb.matchup_sim as MS  # noqa: E402
from fbb.context import AnalysisContext  # noqa: E402
from fbb.improve import TOP_FREE_AGENTS, add_drop_moves  # noqa: E402
from fbb.valuation import player_values  # noqa: E402
from scripts import backtest_sims as B  # noqa: E402

ARMS = ("averages", "courtside", "projections", "recent")
TEAM = 1
MOVES = 2  # pickups a week beyond filling open roster spots, as in backtest_sims.manage_team
RECENT_DAYS = 15
GIVE_UP = 20  # team games in a row a manager waits for a hurt player (about six weeks)


@lru_cache(maxsize=None)
def _per_game(season: B.Season, mp: int, pid: str, recent: bool = False) -> float:
    """Points per game so far (``recent``: over the last RECENT_DAYS, if he played in them), his preseason
    projection until he has played."""
    start = season.mps[mp][0]
    games = season.actual.get(pid, {})
    played = [v for d, v in games.items() if d < start]
    if recent:
        played = [v for d, v in games.items() if start - RECENT_DAYS <= d < start] or played
    return sum(played) / len(played) if played else season.proj[1].get(pid, season.proj[mp][pid]).fp_pg


@lru_cache(maxsize=None)
def _absent(season: B.Season, mp: int, pid: str) -> int:
    """Team games in a row he has missed as of this week."""
    team = season.proj[mp][pid].pro_team
    games = season.actual.get(pid, {})
    missed = 0
    for day in range(season.mps[mp][0] - 1, 0, -1):
        if team in season.schedule.games.get(day, ()):
            if day in games:
                break
            missed += 1
    return missed


@lru_cache(maxsize=None)
def _pickup_value(season: B.Season, mp: int, pid: str, recent: bool = False, horizon: int = 14) -> float:
    """Points per game times his team's games in the next two weeks, less the games he's listed out."""
    start = season.mps[mp][0]
    pr = season.proj[mp][pid]
    games = sum(1 for p in range(start, start + horizon) if pr.pro_team in season.schedule.games.get(p, ()))
    return _per_game(season, mp, pid, recent) * max(0.0, games - pr.games_out)


def _keep_value(season: B.Season, mp: int, pid: str, arm: str) -> float:
    """How a manager ranks whom to let go: points per game, by his own source, until he gives up on a player."""
    if _absent(season, mp, pid) >= GIVE_UP:
        return 0.0
    if arm in ("courtside", "projections"):
        return season.proj[mp][pid].fp_pg
    return _per_game(season, mp, pid, arm == "recent")


def _move(season: B.Season, mp: int, roster: list[str], free: list[str], add: str, drop: str | None) -> tuple:
    free.remove(add)
    roster.append(add)
    if drop:
        roster.remove(drop)
        free.append(drop)
        free.sort(key=lambda p: -B._value(season, mp, p))
    return add, drop


def _courtside(season: B.Season, mp: int, rosters, ir, free: list[str], snapshot) -> list[tuple]:
    """Team 1's week by the app: IR as everyone, open spots to the free agent who adds the most, then the
    top add/drop move while one gains."""
    B.ir_moves(season, mp, TEAM, rosters, ir, free, keep=lambda p: _keep_value(season, mp, p, "courtside"))
    roster = rosters[TEAM]
    moves = []
    while len(roster) < B.ROUNDS and free:
        ctx = AnalysisContext(snapshot(), season.proj[mp])
        values = player_values(ctx)
        fas = sorted((p for p in free if ctx.proj[p].games_out < 6), key=lambda p: -values[p]["ros_value"])
        if not fas:
            break
        mine = ctx.team_roster(TEAM)
        add = max(fas[:TOP_FREE_AGENTS], key=lambda p: ctx.project_window(mine + [p], team_id=TEAM).mean)
        moves.append(_move(season, mp, roster, free, add, None))
    while len(moves) < MOVES:
        ctx = AnalysisContext(snapshot(), season.proj[mp])
        best = add_drop_moves(ctx, TEAM, ctx.project_window(ctx.team_roster(TEAM), team_id=TEAM), limit=1)
        if not best:
            break
        moves.append(_move(season, mp, roster, free, best[0]["add"], best[0]["drop"]))
    return moves


def _by_rule(season: B.Season, mp: int, tid: int, rosters, ir, free: list[str], arm: str) -> list[tuple]:
    """The rivals' rule, each arm by its own numbers."""
    value = None if arm == "projections" else (lambda p: _pickup_value(season, mp, p, arm == "recent"))
    return B.manage_team(season, mp, tid, rosters, ir, free, value=value,
                         keep=lambda p: _keep_value(season, mp, p, arm))


def manage(season: B.Season, mp: int, rosters, ir, rng: np.random.Generator, arm: str, snapshot) -> list[tuple]:
    """One week's moves, in a random order, with team 1 managed by ``arm``. Returns its (added, dropped) pairs."""
    free = B.free_agents(season, mp, rosters, ir)
    mine = []
    for tid in rng.permutation(range(1, B.TEAMS + 1)):
        tid = int(tid)
        if tid != TEAM:
            _by_rule(season, mp, tid, rosters, ir, free, "averages")
        elif arm == "courtside":
            mine = _courtside(season, mp, rosters, ir, free, snapshot)
        else:
            mine = _by_rule(season, mp, tid, rosters, ir, free, arm)
    return mine


def play(season: B.Season, league: int, arm: str) -> dict:
    """One league-season with team 1 managed by ``arm``: its record, points, finish and moves."""
    B.apply_config(B.CONFIGS["app"])
    rng = np.random.default_rng([season.season, league])
    rosters = B.draft(season, rng)
    ir: dict[int, str | None] = {tid: None for tid in rosters}
    records = {tid: [0, 0, 0] for tid in rosters}
    points_for = {tid: 0.0 for tid in rosters}
    schedule = B.round_robin_schedule()
    results: dict[tuple[int, int], str] = {}
    log = []

    def moves(mp: int) -> None:
        def snapshot():
            return season.snapshot(mp, rosters, ir, records, schedule, results)
        log.extend([mp, add, drop] for add, drop in manage(season, mp, rosters, ir, rng, arm, snapshot))

    def scores(mp: int, pairs) -> dict[tuple[int, int], tuple[float, float]]:
        ctx = AnalysisContext(season.snapshot(mp, rosters, ir, records, schedule, results), season.proj[mp])
        sim = MS.MatchupSimulator(ctx, seed=0)
        days = ctx.matchup_days(mp)
        return {(a, b): tuple(B.real_score(season, *sim._plan(ctx.team_roster(t), days, set()))[0] for t in (a, b))
                for a, b in pairs}

    for mp in range(1, B.REG_WEEKS + 1):
        if mp > 1:
            moves(mp)
        for (a, b), (xa, xb) in scores(mp, [(a, b) for m, a, b in schedule if m == mp]).items():
            results[(mp, a)] = "HOME" if xa > xb else ("AWAY" if xb > xa else "TIE")
            points_for[a] += xa
            points_for[b] += xb
            for tid, won in ((a, xa > xb), (b, xb > xa)):
                records[tid][0 if won else (2 if xa == xb else 1)] += 1
    seeds = sorted(records, key=lambda t: (-(records[t][0] + 0.5 * records[t][2]), -points_for[t]))
    pairs = [(seeds[0], seeds[3]), (seeds[1], seeds[2])]
    for mp in (B.REG_WEEKS + 1, B.REG_WEEKS + 2):  # semifinals, then the final, by real scores
        moves(mp)
        winners = [a if xa >= xb else b for (a, b), (xa, xb) in scores(mp, pairs).items()]
        pairs = [tuple(winners)]
    return {"record": records[TEAM], "points_for": points_for[TEAM], "rank": seeds.index(TEAM) + 1,
            "playoffs": TEAM in seeds[:B.PLAYOFF_TEAMS], "champion": winners[0] == TEAM, "moves": log}


def run_league(season: B.Season, league: int, arms=ARMS) -> dict:
    out = {"season": season.season, "league": league, "arms": {}}
    for arm in arms:
        t = time.time()
        out["arms"][arm] = dict(play(season, league, arm), seconds=round(time.time() - t, 1))
    return out


# -- report -------------------------------------------------------------------------------------------

def _wins(r: dict) -> float:
    w, _, t = r["record"]
    return w + 0.5 * t


MEASURES = {  # per league-season, team 1
    "wins": _wins,
    "points_per_week": lambda r: r["points_for"] / B.REG_WEEKS,
    "playoffs": lambda r: float(r["playoffs"]),
    "titles": lambda r: float(r["champion"]),
    "rank": lambda r: float(r["rank"]),
    "moves": lambda r: float(len(r["moves"])),
}
PAIRS = (("courtside", "averages"), ("projections", "averages"), ("courtside", "projections"), ("recent", "averages"))


def summarize(leagues: list[dict]) -> dict:
    rng = np.random.default_rng(0)
    arms = [a for a in ARMS if all(a in lg["arms"] for lg in leagues)]
    seasons = sorted({lg["season"] for lg in leagues})
    out: dict = {"leagues": len(leagues), "seasons": seasons, "arms": {}, "by_season": {}, "differences": {}}
    for arm in arms:
        out["arms"][arm] = {m: float(np.mean([f(lg["arms"][arm]) for lg in leagues])) for m, f in MEASURES.items()}
        out["by_season"][arm] = {s: {m: float(np.mean([f(lg["arms"][arm]) for lg in leagues if lg["season"] == s]))
                                     for m, f in MEASURES.items()} for s in seasons}
    groups = np.arange(len(leagues))
    for x, y in PAIRS:
        if x not in arms or y not in arms:
            continue
        res = {}
        for m, f in MEASURES.items():
            d = np.array([f(lg["arms"][x]) - f(lg["arms"][y]) for lg in leagues])
            res[m] = {"mean": float(d.mean()), "ci": B._bootstrap(lambda i: d[i].mean(), groups, rng)}
        res["by_season"] = {s: {m: float(np.mean([f(lg["arms"][x]) - f(lg["arms"][y])
                                                  for lg in leagues if lg["season"] == s]))
                                for m, f in MEASURES.items()} for s in seasons}
        out["differences"][f"{x} - {y}"] = res
    return out


def report(paths: list[Path]) -> dict:
    leagues = []
    for path in paths:
        for f in (sorted(path.glob("*.json.gz")) if path.is_dir() else [path]):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            leagues.extend(data if isinstance(data, list) else [data])
    return summarize(leagues)


def print_report(s: dict) -> None:
    print(f"\n{s['leagues']} league-seasons ({', '.join(map(str, s['seasons']))}); team 1 of 10, "
          f"{B.REG_WEEKS} weeks, {B.PLAYOFF_TEAMS} make the playoffs")

    def cells(r: dict) -> str:
        return "".join(f"{v:>17.1%}" if m in ("playoffs", "titles") else f"{v:>17.2f}" for m, v in r.items())

    print(f"\n{'team 1 managed by':<24}" + "".join(f"{m:>17}" for m in MEASURES))
    for arm, r in s["arms"].items():
        print(f"{arm:<24}" + cells(r))
        for season, by in s["by_season"][arm].items():
            print(f"  {season:<22}" + cells(by))
    print("\nLeague by league (95% interval, these three seasons)")
    for name, res in s["differences"].items():
        row = []
        for m in MEASURES:
            r = res[m]
            fmt = (lambda v: f"{v * 100:+.1f}") if m in ("playoffs", "titles") else (lambda v: f"{v:+.2f}")
            row.append(f"{fmt(r['mean'])} [{fmt(r['ci'][0])},{fmt(r['ci'][1])}]".rjust(26))
        print(f"{name:<24}" + "".join(row))
        for season, by in res["by_season"].items():
            print(f"  {season}: " + ", ".join(
                f"{m} {by[m] * 100:+.1f}" if m in ("playoffs", "titles") else f"{m} {by[m]:+.2f}"
                for m in ("wins", "points_per_week", "playoffs", "titles")))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--season", type=int, required=True)
    r.add_argument("--leagues", default="0")
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--out", type=Path, required=True)
    rep = sub.add_parser("report")
    rep.add_argument("paths", type=Path, nargs="+")
    rep.add_argument("--json", type=Path)
    args = ap.parse_args()
    if args.cmd == "run":
        season = B.load_season(args.season)
        args.out.mkdir(parents=True, exist_ok=True)
        for league in B._range(args.leagues):
            t = time.time()
            res = run_league(season, league, [a for a in args.arms.split(",") if a])
            with gzip.open(args.out / f"{args.season}_{league}_g0.json.gz", "wt", encoding="utf-8") as f:
                json.dump(res, f)
            secs = ", ".join(f"{a} {r['seconds']:.0f}s" for a, r in res["arms"].items())
            print(f"{args.season} league {league}: {time.time() - t:.0f}s ({secs})", flush=True)
    else:
        s = report(args.paths)
        if args.json:
            args.json.write_text(json.dumps(s, indent=1))
        print_report(s)


if __name__ == "__main__":
    main()
