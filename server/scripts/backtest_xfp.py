"""Learn and test how far the in-season update should trust xFP over what actually happened.

For 2023-24, 2024-25 and 2025-26, at each cutoff (days after opening night, scripts.fetch_tracking), every
player with 40+ games is projected the way the app would have done it: an out-of-sample preseason
projection, then the engine's in-season update over his games so far (with last season's closing games).
That runs once as the app does now and once with xFP, and both are compared with his fantasy points per
game over the rest of the season. Nothing leaks: a season's shot model is trained on earlier seasons only,
established rates come from the two seasons before, and each season's xFP weights (BETA, per stat) are
fitted on the other two seasons. The saved weights are fitted on all three.

FP/G is linear in each weight (rates are linear in the evidence, FP is linear in rates), so every case
needs one engine run per stat group and the weights are fitted on a fine grid.

It also checks the claim behind a buy-low list: players whose FP/G so far ran above their xFP/G should
fall back over the rest of the season, and players who ran below should pick up.

Usage: python -m scripts.backtest_xfp
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.backtest_recency as BR  # noqa: E402
from fbb.history import HISTORY_DIR, load_history  # noqa: E402
from fbb.models import GameLog, LeagueSettings, LeagueSnapshot, Player, ProSchedule  # noqa: E402
from fbb.projections import ProjectionEngine  # noqa: E402
from fbb.xfp import (BETA_GROUPS, ShotModel, calibrate, calibrate_lines, league_rates, save_bundle,  # noqa: E402
                     shot_totals, skills, tracking_line, xfp_per_game)
from scripts.fetch_tracking import CUTOFF_DAYS, dash_path, load_dash, load_shots, opening_day, shots_path  # noqa: E402

SEASONS = (2024, 2025, 2026)
FIRST_SHOT_SEASON = 2021
GROUPS = list(BETA_GROUPS)
GRID = np.round(np.arange(0.0, 1.0001, 0.05), 2)


def shots_before(season: int) -> list[list]:
    return [s for y in range(FIRST_SHOT_SEASON, season) if shots_path(y).exists() for s in load_shots(y)]


def shot_report(model: ShotModel, train: list[list], test: list[list]) -> dict:
    """Log loss and Brier score of the shot model against league FG% by zone (and 2 vs 3), on unseen shots."""
    zone: dict[tuple, list[int]] = {}
    for s in train:
        zone.setdefault((s[7], s[6]), []).append(s[11])
    zone_rate = {k: sum(v) / len(v) for k, v in zone.items()}
    y = np.array([s[11] for s in test])
    p_model = np.clip(model.predict(test), 1e-4, 1 - 1e-4)
    p_zone = np.clip(np.array([zone_rate.get((s[7], s[6]), y.mean()) for s in test]), 1e-4, 1 - 1e-4)

    def scores(p):
        return {"log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
                "brier": float(np.mean((p - y) ** 2))}
    return {"shots": len(test), "model": scores(p_model), "zone_only": scores(p_zone),
            "fg_pct": float(y.mean()), "model_mean": float(p_model.mean())}


def season_cases(season: int) -> tuple[list[dict], dict]:
    t0 = time.time()
    train = shots_before(season)
    shot_model = ShotModel().fit(train)
    cur_shots = load_shots(season)
    report = shot_report(shot_model, train, cur_shots)
    cur_p = shot_model.predict(cur_shots)

    prior = []
    for y in (season - 1, season - 2):
        dash = load_dash(y, "full") if dash_path(y, "full").exists() else {}
        shots = load_shots(y) if shots_path(y).exists() else []
        prior.append((dash, shot_totals(shots, calibrate(shots, shot_model.predict(shots)))))
    lg = league_rates(prior[0][0])
    ids = json.loads((HISTORY_DIR / "nba_ids.json").read_text())

    ds = load_history()
    logs = {pid: g for pid, g in BR.season_logs(season).items() if len(g) >= BR.MIN_GAMES}
    players = {}
    for pid in logs:
        espn = ds.espn.get((pid, season))
        players[pid] = Player(id=pid, name=ds.names.get(pid, pid), age=ds.age_in(pid, season),
                              history=[h for h in ds.lines.get(pid, []) if h.season < season],
                              experts=[espn] if espn else [], adp=ds.adp.get((pid, season)))
    pre = BR.preseason(season, set(players))
    closing = BR.closing_games(BR.season_logs(season - 1))
    snap = LeagueSnapshot(settings=LeagueSettings(name="backtest", season=season), teams=[], players=players,
                          schedule=ProSchedule(season=season), current_period=10_000)
    engine = ProjectionEngine(snap)
    fp = engine.fp_scoring.fantasy_points

    opening = opening_day(season)
    cases, gp_agree, gp_total = [], 0, 0
    for days in CUTOFF_DAYS:
        dash = load_dash(season, f"d{days}")
        through = int((opening + timedelta(days=days - 1)).strftime("%Y%m%d"))
        totals = shot_totals(cur_shots, calibrate(cur_shots, cur_p, through), through)
        # Every player's line first, so the league's expected stats can be calibrated as the app does.
        lines = calibrate_lines({
            nid: tracking_line(season, row, totals.get(nid), skills([(prior[0][0].get(nid), prior[0][1].get(nid)),
                                                                     (prior[1][0].get(nid), prior[1][1].get(nid))], lg))
            for nid, row in dash.items() if row.get("MIN")})
        for nid, row in dash.items():
            pid = ids.get(str(nid))
            if pid not in logs or pid not in pre or nid not in lines:
                continue
            games = logs[pid]
            seen = [g for g in games if g["period"] <= days]  # ESPN scoring period 1 is opening night
            rest = [g for g in games if g["period"] > days]
            gp_total += 1
            gp_agree += len(seen) == row["GP"]
            if not seen or len(rest) < BR.MIN_LEFT:
                continue
            line = lines[nid]
            player =players[pid].model_copy(update={
                "recent_games": [GameLog(**g) for g in closing.get(pid, []) + seen], "tracking": line})
            # FP/G with no xFP, then the change from each stat group at full weight.
            engine.xfp_beta = {}
            base = engine._in_season(player, pre[pid]).fp_pg
            deltas = []
            for g in GROUPS:
                engine.xfp_beta = {k: 1.0 for k in BETA_GROUPS[g]}
                deltas.append(engine._in_season(player, pre[pid]).fp_pg - base)
            so_far, expected = xfp_per_game(line, engine.fp_scoring)
            cases.append(dict(season=season, days=days, pid=pid, base=base, deltas=deltas,
                              actual=statistics.fmean(fp(g["stats"]) for g in rest), so_far=so_far,
                              luck=so_far - expected))
    report["gp_match"] = round(gp_agree / gp_total, 3) if gp_total else None
    print(f"{season - 1}-{str(season)[2:]}: {len(cases)} player-cutoffs, games-played agreement between NBA.com "
          f"and ESPN {report['gp_match']:.1%}, {time.time() - t0:.0f}s")
    return cases, report


def arrays(cases: list[dict]):
    base = np.array([c["base"] for c in cases])
    D = np.array([c["deltas"] for c in cases])
    actual = np.array([c["actual"] for c in cases])
    keys = [(c["season"], c["days"]) for c in cases]
    return base, D, actual, keys


def mae(pred: np.ndarray, actual: np.ndarray, keys: list[tuple]) -> float:
    """Mean over (season, cutoff) of each cutoff's mean absolute error, like scripts.backtest_recency."""
    err = np.abs(pred - actual)
    groups: dict[tuple, list[float]] = {}
    for k, e in zip(keys, err):
        groups.setdefault(k, []).append(e)
    return float(np.mean([np.mean(v) for v in groups.values()]))


def fit_beta(cases: list[dict]) -> dict[str, float]:
    """Coordinate descent over the grid, one stat group at a time, until nothing moves."""
    base, D, actual, keys = arrays(cases)
    beta = np.zeros(len(GROUPS))
    for _ in range(6):
        moved = False
        for j in range(len(GROUPS)):
            best = min(GRID, key=lambda b: mae(base + D @ np.where(np.arange(len(GROUPS)) == j, b, beta), actual, keys))
            moved |= best != beta[j]
            beta[j] = best
        if not moved:
            break
    return {g: float(b) for g, b in zip(GROUPS, beta)}


def predict(cases: list[dict], beta: dict[str, float]) -> np.ndarray:
    base, D, _, _ = arrays(cases)
    return base + D @ np.array([beta[g] for g in GROUPS])


def luck_report(cases: list[dict]) -> dict:
    """Does running above xFP so far mean falling back later? Slope of (rest of season - so far) on luck."""
    luck = np.array([c["luck"] for c in cases])
    change = np.array([c["actual"] - c["so_far"] for c in cases])
    slope = float(np.polyfit(luck, change, 1)[0])
    out = {"slope": slope, "corr": float(np.corrcoef(luck, change)[0, 1])}
    hot = [c for c in cases if c["luck"] >= 3.0]
    cold = [c for c in cases if c["luck"] <= -3.0]
    out["hot_n"], out["cold_n"] = len(hot), len(cold)
    out["hot_fell"] = float(np.mean([c["actual"] < c["so_far"] for c in hot])) if hot else None
    out["cold_rose"] = float(np.mean([c["actual"] > c["so_far"] for c in cold])) if cold else None
    out["all_fell"] = float(np.mean(change < 0))
    return out


def main() -> None:
    t0 = time.time()
    all_cases, shot_reports = [], {}
    for season in SEASONS:
        cases, rep = season_cases(season)
        all_cases += cases
        shot_reports[season] = rep

    print("\nShot model on each season's unseen shots (trained on earlier seasons):")
    print(f"{'season':<9}{'shots':>8}{'log loss':>10}{'zone only':>11}{'Brier':>8}{'zone only':>11}{'FG%':>7}{'xFG%':>7}")
    for s, r in shot_reports.items():
        print(f"{s - 1}-{str(s)[2:]:<4}{r['shots']:>8}{r['model']['log_loss']:>10.4f}{r['zone_only']['log_loss']:>11.4f}"
              f"{r['model']['brier']:>8.4f}{r['zone_only']['brier']:>11.4f}{r['fg_pct']:>7.3f}{r['model_mean']:>7.3f}")

    print("\nRest-of-season FP/G error, each season's xFP weights fitted on the other two seasons:")
    print(f"{'season':<9}{'app now':>9}{'with xFP':>10}{'change':>9}" + "".join(f"{f'day {d}':>9}" for d in CUTOFF_DAYS))
    by_season = {}
    for s in SEASONS:
        test = [c for c in all_cases if c["season"] == s]
        beta = fit_beta([c for c in all_cases if c["season"] != s])
        _, _, actual, keys = arrays(test)
        now = mae(np.array([c["base"] for c in test]), actual, keys)
        pred = predict(test, beta)
        new = mae(pred, actual, keys)
        per_day = []
        for d in CUTOFF_DAYS:
            idx = [i for i, c in enumerate(test) if c["days"] == d]
            a = float(np.mean(np.abs(np.array([test[i]["base"] for i in idx]) - actual[idx])))
            b = float(np.mean(np.abs(pred[idx] - actual[idx])))
            per_day.append((a, b))
        by_season[s] = {"app_now": now, "with_xfp": new, "beta": beta, "by_day": per_day}
        print(f"{s - 1}-{str(s)[2:]:<4}{now:>9.3f}{new:>10.3f}{(new - now) / now:>+9.1%}"
              + "".join(f"{(b - a) / a:>+9.1%}" for a, b in per_day))
        print(f"{'':<9}weights: " + ", ".join(f"{g} {b:.2f}" for g, b in beta.items()))

    beta = fit_beta(all_cases)
    luck = luck_report(all_cases)
    print("\nWeights fitted on all three seasons (saved): " + ", ".join(f"{g} {b:.2f}" for g, b in beta.items()))
    print(f"\nLuck (FP/G so far minus xFP/G so far) vs what happened next, {len(all_cases)} player-cutoffs:")
    print(f"  each point of luck so far came with {luck['slope']:+.2f} FP/G of change over the rest of the season "
          f"(correlation {luck['corr']:+.2f})")
    print(f"  {luck['hot_n']} ran 3+ FP/G above xFP: {luck['hot_fell']:.0%} scored less the rest of the way "
          f"(all players: {luck['all_fell']:.0%})")
    print(f"  {luck['cold_n']} ran 3+ FP/G below xFP: {luck['cold_rose']:.0%} scored more the rest of the way "
          f"(all players: {1 - luck['all_fell']:.0%})")

    # For the season in progress: a shot model on every finished season, and each player's established rates.
    coming = SEASONS[-1] + 1
    final_model = ShotModel().fit(shots_before(coming))
    prior = []
    for y in (coming - 1, coming - 2):
        shots = load_shots(y)
        prior.append((load_dash(y, "full"), shot_totals(shots, calibrate(shots, final_model.predict(shots)))))
    lg = league_rates(prior[0][0])
    skill_table = {str(nid): skills([(prior[0][0].get(nid), prior[0][1].get(nid)),
                                     (prior[1][0].get(nid), prior[1][1].get(nid))], lg)
                   for nid in set(prior[0][0]) | set(prior[1][0])}
    metrics = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seasons": list(SEASONS),
        "cutoff_days": list(CUTOFF_DAYS),
        "shot_model": {str(k): v for k, v in shot_reports.items()},
        "by_season": {str(k): v for k, v in by_season.items()},
        "luck": luck,
    }
    save_bundle(final_model, beta, lg, metrics, skill_table, coming)
    print(f"\nsaved -> data/model/xfp_model.joblib ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
