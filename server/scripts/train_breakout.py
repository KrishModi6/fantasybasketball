"""Train the breakout model (fbb/breakout.py) and backtest it against simple ways of picking breakouts.

One row per player and season from 2018-19 on, for players with a level to break out from (20+ games the
season before), with features known before the season and whether it turned out a breakout season.

Rolling-origin backtest: the model for season T is trained on seasons before T only. It is compared, on the
same players, with ranking them by

  youth    youngest first
  minutes  biggest jump in minutes ESPN projects over last season
  sps      biggest gain the stat baseline projects
  espn     biggest gain ESPN projects (the published opinion to beat)

and scored on how well each orders the players (AUC, average precision), how many of its top 25 broke out,
and, for the model, whether its probabilities mean what they say (Brier score, calibration).

Seasons whose stored ESPN "projection" was refreshed in-season are found the way scripts/train_model.py finds
them and their ESPN features left blank, so no in-season information leaks in.

Writes data/model/breakout_model.joblib and data/model/breakout_metrics.json.

Usage:  python -m scripts.train_breakout
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.baseline import league_rates  # noqa: E402
from fbb.breakout import (FEATURES, MIN_GAIN, MIN_GAMES, MIN_LEVEL, MIN_PRIOR_GAMES, BreakoutModel, is_breakout,  # noqa: E402
                          matrix, preseason_rows)
from fbb.history import load_history  # noqa: E402
from scripts.train_model import audit_espn  # noqa: E402

OUT = ROOT / "data" / "model"
FIRST_SEASON = 2019  # the first season with a season before it in the history file
LAST_SEASON = 2026  # the last completed season
BACKTEST_SEASONS = list(range(2022, LAST_SEASON + 1))
TOP_N = 25
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=6, min_child_samples=40, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, verbose=-1, seed=7)
ROUNDS = 220
BASELINES = {"youth": lambda f: -f["age"], "minutes": lambda f: f["espn_mpg_gain"], "sps": lambda f: f["sps_gain"],
             "espn": lambda f: f["espn_gain"]}


def build_rows(ds, lg) -> list[dict]:
    rows = []
    for season in range(FIRST_SEASON, LAST_SEASON + 1):
        for pid, feats in preseason_rows(ds, season, lg).items():
            by = {h.season: h for h in ds.lines[pid]}
            rows.append({"pid": pid, "season": season, "feats": feats,
                         "y": int(bool(is_breakout(by.get(season - 1), by.get(season))))})
    return rows


def fit(rows: list[dict]) -> lgb.Booster:
    data = lgb.Dataset(matrix([r["feats"] for r in rows]), np.array([r["y"] for r in rows]), feature_name=FEATURES,
                       free_raw_data=False)
    return lgb.train(PARAMS, data, num_boost_round=ROUNDS)


def auc(y: np.ndarray, score: np.ndarray) -> float:
    """Chance a random breakout is ranked above a random non-breakout (ties count half)."""
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    for v in np.unique(score):  # average the ranks of ties
        tie = score == v
        if tie.sum() > 1:
            ranks[tie] = ranks[tie].mean()
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)) if n_pos and n_neg else float("nan")


def average_precision(y: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(-score, kind="mergesort")
    hits = y[order]
    precision = np.cumsum(hits) / np.arange(1, len(hits) + 1)
    return float((precision * hits).sum() / max(hits.sum(), 1))


def rank_metrics(y: np.ndarray, score: np.ndarray) -> dict:
    top = np.argsort(-score, kind="mergesort")[:TOP_N]
    return {"auc": auc(y, score), "average_precision": average_precision(y, score), "top_hits": int(y[top].sum()),
            "top_n": TOP_N}


def platt(raw: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Slope and intercept that turn raw scores into probabilities that match what happened."""
    def loss(z):
        p = np.clip(1 / (1 + np.exp(-(z[0] * raw + z[1]))), 1e-9, 1 - 1e-9)
        return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())

    res = minimize(loss, np.array([1.0, 0.0]), method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-9})
    return float(res.x[0]), float(res.x[1])


def calibration_table(y: np.ndarray, p: np.ndarray, edges=(0.0, 0.05, 0.10, 0.20, 0.35, 1.0)) -> list[dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.any():
            out.append({"range": f"{lo:.0%}-{hi:.0%}", "players": int(m.sum()), "predicted": float(p[m].mean()),
                        "broke_out": float(y[m].mean())})
    return out


def main() -> None:
    t0 = time.time()
    ds = load_history()
    if not ds.lines:
        sys.exit("No history found. Run: python -m scripts.build_history")
    excluded, _ = audit_espn(ds)
    for key in [k for k in ds.espn if k[1] in excluded]:
        del ds.espn[key]
    lg = league_rates([ln for ln in ds.all_lines() if ln.season <= LAST_SEASON])
    rows = build_rows(ds, lg)
    base_rate = float(np.mean([r["y"] for r in rows]))
    print(f"{len(rows)} player-seasons, {sum(r['y'] for r in rows)} breakouts ({base_rate:.1%}); "
          f"ESPN seasons left blank (refreshed in-season): {excluded}")

    # ---- rolling-origin backtest ---------------------------------------------------------------------
    by_season: dict[int, dict] = {}
    oof_raw, oof_y, oof_season = [], [], []
    for T in BACKTEST_SEASONS:
        train = [r for r in rows if r["season"] < T]
        test = [r for r in rows if r["season"] == T]
        raw = fit(train).predict(matrix([r["feats"] for r in test]), raw_score=True)
        y = np.array([r["y"] for r in test])
        rec = {"players": len(test), "breakouts": int(y.sum()), "model": rank_metrics(y, raw)}
        # Baselines on the players they can rank; the model on the same players beside each.
        for name, key in BASELINES.items():
            score = np.array([key(r["feats"]) for r in test])
            ok = ~np.isnan(score)
            if ok.sum() >= 100 and y[ok].sum() >= 5:
                rec[name] = {**rank_metrics(y[ok], score[ok]), "players": int(ok.sum()),
                             "model_same_players": rank_metrics(y[ok], raw[ok])}
        by_season[T] = rec
        oof_raw += list(raw)
        oof_y += list(y)
        oof_season += [T] * len(test)
        print(f"  {T}: trained on {len(train)}, tested on {len(test)} ({int(y.sum())} breakouts)")
    oof_raw, oof_y, oof_season = np.array(oof_raw), np.array(oof_y), np.array(oof_season)

    # Calibration for season T is fitted on the other backtest seasons' out-of-fold scores.
    prob = np.zeros(len(oof_raw))
    for T in BACKTEST_SEASONS:
        slope, intercept = platt(oof_raw[oof_season != T], oof_y[oof_season != T])
        m = oof_season == T
        prob[m] = 1 / (1 + np.exp(-(slope * oof_raw[m] + intercept)))
    brier = float(np.mean((prob - oof_y) ** 2))
    brier_base = float(np.mean((oof_y.mean() - oof_y) ** 2))

    def mean_of(name: str, key: str, sub: str | None = None) -> float | None:
        vals = [(by_season[T][name][sub] if sub else by_season[T][name])[key] for T in BACKTEST_SEASONS if name in by_season[T]]
        return float(np.mean(vals)) if vals else None

    summary = {"model": {k: mean_of("model", k) for k in ("auc", "average_precision", "top_hits")}}
    for name in BASELINES:
        if any(name in by_season[T] for T in BACKTEST_SEASONS):
            summary[name] = {k: mean_of(name, k) for k in ("auc", "average_precision", "top_hits")}
            summary[name]["model_same_players"] = {k: mean_of(name, k, "model_same_players")
                                                   for k in ("auc", "average_precision", "top_hits")}
            summary[name]["seasons"] = [T for T in BACKTEST_SEASONS if name in by_season[T]]
    print(f"\nBacktest {BACKTEST_SEASONS[0]}-{BACKTEST_SEASONS[-1]} (means over seasons; top {TOP_N} = breakouts among each method's top {TOP_N})")
    print(f"{'method':<10}{'AUC':>8}{'avg prec':>10}{'top hits':>10}   {'model, same players: AUC / avg prec / top hits'}")
    for name, v in summary.items():
        same = v.get("model_same_players")
        tail = f"   {same['auc']:.3f} / {same['average_precision']:.3f} / {same['top_hits']:.1f}" if same else ""
        print(f"{name:<10}{v['auc']:>8.3f}{v['average_precision']:>10.3f}{v['top_hits']:>10.1f}{tail}")
    table = calibration_table(oof_y, prob)
    print(f"\nBrier score {brier:.4f} (always saying the base rate: {brier_base:.4f}). Calibration:")
    for row in table:
        print(f"  predicted {row['range']:>9}: {row['players']:>4} players, mean {row['predicted']:.1%}, broke out {row['broke_out']:.1%}")

    # ---- final model -----------------------------------------------------------------------------------
    booster = fit(rows)
    calibration = platt(oof_raw, oof_y)
    gain = booster.feature_importance("gain")
    importance = sorted(((f, float(g / gain.sum())) for f, g in zip(FEATURES, gain)), key=lambda x: -x[1])[:10]
    metrics = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "definition": {"min_prior_games": MIN_PRIOR_GAMES, "min_games": MIN_GAMES, "min_gain": MIN_GAIN,
                       "min_level": MIN_LEVEL, "scoring": "ESPN default H2H points"},
        "rows": len(rows), "breakouts": int(sum(r["y"] for r in rows)), "base_rate": base_rate,
        "train_seasons": [FIRST_SEASON, LAST_SEASON], "backtest_seasons": BACKTEST_SEASONS,
        "excluded_espn_seasons": excluded, "summary": summary, "by_season": {str(k): v for k, v in by_season.items()},
        "brier": brier, "brier_base_rate": brier_base, "calibration": table, "platt": list(calibration),
        "feature_importance": importance, "lgb_params": {**PARAMS, "num_boost_round": ROUNDS},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump({"booster": booster, "features": FEATURES, "calibration": calibration, "metrics": metrics},
                OUT / "breakout_model.joblib")
    (OUT / "breakout_metrics.json").write_text(json.dumps(metrics, indent=2))

    # ---- this season's candidates (a look, not a test) -------------------------------------------------
    BreakoutModel.load.cache_clear()
    model = BreakoutModel.load()
    target = LAST_SEASON + 1
    now = preseason_rows(ds, target, league_rates(ds.all_lines()))
    ids = list(now)
    p = model.predict([now[i] for i in ids])
    print(f"\nTop breakout candidates for {target - 1}-{str(target)[2:]} ({len(ids)} players):")
    for i in np.argsort(-p)[:15]:
        f = now[ids[i]]
        why = ", ".join(f"{r['factor']} {'+' if r['effect'] > 0 else '-'}" for r in model.reasons(f))
        print(f"  {p[i]:5.1%}  {ds.names[ids[i]]:<24} age {f['age']:.0f}  last {f['l1_fp_pg']:.1f} FP/G in {f['l1_mpg']:.0f} min  ({why})")
    print(f"\nsaved -> {OUT}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
