"""Backtest how much the in-season update should trust recent games versus older evidence.

For each player with 40+ games in a completed season: start from the preseason projection the app
would have made (the ML model is refit on earlier seasons only, then blended with SPS and ESPN using the
learned weights, so nothing leaks from the season being tested), feed in his first N games through the
engine's own in-season update, and compare the projected fantasy points per game with his actual
average over the rest of that season. Like the app, the update also sees the closing games of the season
before (his games among his team's last 20). Settings are the half-lives, prior strength and the weight
of last season's closing games in fbb/projections.py; the first row is what the app uses now.

Usage: python -m scripts.backtest_recency --season 2026 --season 2025
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fbb.projections as P  # noqa: E402
import scripts.train_model as TM  # noqa: E402
from fbb.baseline import RATE_STATS, league_rates  # noqa: E402
from fbb.connectors.espn import EspnClient, parse_player  # noqa: E402
from fbb.history import CACHE_DIR, load_history  # noqa: E402
from fbb.models import GameLog, LeagueSettings, LeagueSnapshot, Player, ProSchedule  # noqa: E402

CUTOFFS = (0, 1, 3, 5, 10, 20, 35)  # games seen before projecting
MIN_GAMES, MIN_LEFT = 40, 10
SETTINGS = {  # changes to the settings in fbb/projections.py; the first row is the app as it is
    "app now": {},
    "no closing games": {"CARRYOVER_RATE_WEIGHT": 0.0, "CARRYOVER_MINUTES_WEIGHT": 0.0},
    "closing games x0.5": {"CARRYOVER_RATE_WEIGHT": 0.5, "CARRYOVER_MINUTES_WEIGHT": 0.5},
    "all 20 closing games": {"CARRYOVER_GAMES": 20},
    "faster recency": {"RATE_HALF_LIFE_GAMES": 12.0, "MINUTES_HALF_LIFE_GAMES": 5.0},
    "faster minutes only": {"MINUTES_HALF_LIFE_GAMES": 5.0},
    "stronger prior": {"PRIOR_STRENGTH_SCALE": 2.0},
    "no recency weighting": {"RATE_HALF_LIFE_GAMES": 1e9, "MINUTES_HALF_LIFE_GAMES": 1e9,
                             "CARRYOVER_RATE_WEIGHT": 0.0, "CARRYOVER_MINUTES_WEIGHT": 0.0},
}


def season_logs(season: int) -> dict[str, list[dict]]:
    """Every game of a completed season for the 350 most-rostered players (cached after the first run)."""
    path = CACHE_DIR / f"game_logs_{season}_full.json"
    if path.exists():
        return json.loads(path.read_text())
    out = {}
    for entry in EspnClient().player_pool(season, limit=350, recent_games=200):
        p = parse_player(entry, season)
        games = sorted((g.model_dump(mode="json") for g in p.recent_games if g.season == season), key=lambda g: g["period"])
        if games:
            out[p.id] = games
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def closing_games(logs: dict[str, list[dict]], team_games: int = 20) -> dict[str, list[dict]]:
    """Each player's games among his team's last ``team_games`` games of the season, as ESPN returns them
    in the first weeks of the next season (a player who sat out the end of the season has fewer). The
    engine keeps the last CARRYOVER_GAMES of them."""
    periods: dict[str, set[int]] = {}
    for games in logs.values():
        for g in games:
            periods.setdefault(g["team"], set()).add(g["period"])
    out = {}
    for pid, games in logs.items():
        last = sorted(periods.get(games[-1]["team"], ()))[-team_games:]
        if last:
            out[pid] = [g for g in games if g["period"] >= last[0]]
    return out


def preseason(season: int, pids: set[str]) -> dict[str, dict]:
    """Out-of-sample preseason projections in the shape the engine's in-season update expects."""
    ds = load_history()
    lg = league_rates([ln for ln in ds.all_lines() if ln.season < 2027])
    excluded, _ = TM.audit_espn(ds)
    rows, _ = TM.build_rows(ds, lg)
    rows = [r for r in rows if r["espn"] is None or r["season"] not in excluded]
    test = [r for r in rows if r["season"] == season and r["pid"] in pids]
    boosters = {t: TM.fit_target([r for r in rows if r["season"] < season], t) for t in TM.TARGETS}
    preds = TM.predict_rows(boosters, test)
    weights = json.loads((ROOT / "data" / "model" / "metrics.json").read_text())["ensemble_weights"]
    out = {}
    for i, r in enumerate(test):
        ml = {t: float(preds[t][i]) for t in TM.TARGETS}
        ml["mpg"] = min(max(ml["mpg"], 3.0), 40.0)
        ens = {t: TM.blend(TM.source_values(r, ml), weights, t) for t in TM.TARGETS}
        blend = {"mpg": ens["mpg"], "avail": ens["avail"], **{k: ens[k] / 36 for k in RATE_STATS}}  # per 36 -> per minute
        out[r["pid"]] = {"blend": blend, "feats": r["feats"], "has_ml": False, "sources": []}
    return out


def run(season: int) -> None:
    ds = load_history()
    logs = {pid: g for pid, g in season_logs(season).items() if len(g) >= MIN_GAMES}
    players = {}
    for pid in logs:
        espn = ds.espn.get((pid, season))
        players[pid] = Player(id=pid, name=ds.names.get(pid, pid), age=ds.age_in(pid, season),
                              history=[h for h in ds.lines.get(pid, []) if h.season < season],
                              experts=[espn] if espn else [], adp=ds.adp.get((pid, season)))
    snap = LeagueSnapshot(settings=LeagueSettings(name="backtest", season=season), teams=[], players=players,
                          schedule=ProSchedule(season=season))
    engine = P.ProjectionEngine(snap)  # loads the learned prior strengths
    pre = preseason(season, set(players))
    logs = {pid: g for pid, g in logs.items() if pid in pre}
    closing = closing_games(season_logs(season - 1))
    fp = engine.fp_scoring.fantasy_points

    print(f"\n{season - 1}-{str(season)[2:]}: {len(players)} players with {MIN_GAMES}+ games, "
          f"{sum(1 for pid in logs if closing.get(pid))} with closing games from {season - 2}-{str(season - 1)[2:]}")
    print(f"{'settings':<22}{'mean error':>11}" + "".join(f"{f'after {c}':>10}" for c in CUTOFFS))
    saved = {k: getattr(P, k) for changes in SETTINGS.values() for k in changes}
    for name, changes in SETTINGS.items():
        for k, v in {**saved, **changes}.items():
            setattr(P, k, v)
        errors = []
        for c in CUTOFFS:
            err = []
            for pid, games in logs.items():
                if len(games) - c < MIN_LEFT:
                    continue
                seen = [GameLog(**g) for g in closing.get(pid, []) + games[:c]]
                actual = statistics.fmean(fp(g["stats"]) for g in games[c:])
                err.append(abs(engine._in_season(players[pid].model_copy(update={"recent_games": seen}), pre[pid]).fp_pg
                               - actual))
            errors.append(statistics.fmean(err))
        print(f"{name:<22}{statistics.fmean(errors):>11.3f}" + "".join(f"{e:>10.2f}" for e in errors))
    for k, v in saved.items():
        setattr(P, k, v)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, action="append", help="completed season(s), e.g. 2026 for 2025-26")
    for season in ap.parse_args().season or [2026, 2025]:
        run(season)
    print("\nMean absolute error in fantasy points per game (ESPN default scoring) over each player's rest of season.")


if __name__ == "__main__":
    main()
