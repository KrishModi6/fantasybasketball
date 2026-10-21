"""Train the projection model and backtest it against simple and expert baselines.

Pipeline
  1. Build one row per (player, season) with preseason-knowable features.
  2. Rolling-origin backtest: for each test season T, fit on seasons < T and
     predict T. Compare naive (last season), SPS, ESPN, ML, and the blended
     ensemble on fantasy points per game and season totals.
  3. Fit convex ensemble weights on out-of-fold predictions, estimate the
     in-season prior strength for every stat from the ensemble's own errors,
     train final models on all seasons and save everything to
     data/model/projection_model.joblib and data/model/metrics.json.

Usage:  python -m scripts.train_model
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.baseline import RATE_STATS, line_rates, per_game_from_rates, rates_from_per_game, league_rates  # noqa: E402
from fbb.constants import season_games  # noqa: E402
from fbb.history import load_history  # noqa: E402
from fbb.ml.features import build_features, feature_names  # noqa: E402
from fbb.models import ScoringSettings  # noqa: E402

OUT = ROOT / "data" / "model"
TARGETS = ["mpg", "avail"] + RATE_STATS
FIRST_TARGET_SEASON = 2019
BACKTEST_SEASONS = list(range(2021, 2027))
REPORT_SEASONS = list(range(2022, 2027))
SCORING = ScoringSettings()  # ESPN default points (the reference league's settings)

LGB_PARAMS = dict(
    objective="regression", learning_rate=0.03, num_leaves=15, min_child_samples=30,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=3.0, verbose=-1, seed=7,
)
N_ROUNDS = 260


def build_rows(ds, lg):
    rows = []
    seasons = sorted({s for (_, s) in ds.gp})
    for (pid, season), gp in ds.gp.items():
        if season < FIRST_TARGET_SEASON:
            continue
        hist = ds.lines.get(pid, [])
        age = ds.age_in(pid, season)
        debut = ds.debut_year.get(pid)
        years_pro = season - debut if debut else None
        espn = ds.espn.get((pid, season))
        feats, base = build_features(hist, season, age, years_pro, espn, ds.adp.get((pid, season)), lg)
        actual = next((h for h in hist if h.season == season), None)
        prior_ok = any(h.season < season and h.minutes >= 500 for h in hist)
        y: dict[str, float] = {}
        w: dict[str, float] = {}
        if actual and actual.minutes >= 250:
            tot = line_rates(actual)
            for k in RATE_STATS:
                y[k] = tot[k] / actual.minutes * 36
                w[k] = min(actual.minutes, 2500.0)
        if actual and actual.gp >= 5:
            y["mpg"] = actual.minutes / actual.gp
            w["mpg"] = actual.gp
        if prior_ok or espn is not None:
            y["avail"] = min(1.0, gp / season_games(season))
            w["avail"] = 1.0
        rows.append(dict(pid=pid, season=season, feats=feats, base=base, espn=espn, y=y, w=w, actual=actual))
    return rows, seasons


def fit_target(train_rows, target):
    data = [r for r in train_rows if target in r["y"]]
    X = np.array([[r["feats"].get(f, np.nan) for f in feature_names()] for r in data])
    base = np.array([_base(r, target) for r in data])
    y = np.array([r["y"][target] for r in data]) - base
    w = np.array([r["w"][target] for r in data])
    ds = lgb.Dataset(X, y, weight=w, free_raw_data=False)
    return lgb.train(LGB_PARAMS, ds, num_boost_round=N_ROUNDS)


def _base(r, target):
    if target == "mpg":
        return r["feats"]["sps_mpg"]
    if target == "avail":
        return r["feats"]["sps_avail"]
    return r["feats"][f"sps_{target}"]


def predict_rows(boosters, rows):
    X = np.array([[r["feats"].get(f, np.nan) for f in feature_names()] for r in rows])
    out = {}
    for t, b in boosters.items():
        out[t] = np.array([_base(r, t) for r in rows]) + b.predict(X)
    return out


def source_values(r, ml_pred=None):
    """Per-source estimates of each target for one row: {source: {target: value}}."""
    f = r["feats"]
    src = {"sps": {"mpg": f["sps_mpg"], "avail": f["sps_avail"], **{k: f[f"sps_{k}"] for k in RATE_STATS}}}
    if r["espn"] is not None:
        e = {"mpg": f["espn_mpg"], "avail": min(1.0, f["espn_gp_frac"])}
        for k in RATE_STATS:
            if not math.isnan(f[f"espn_{k}"]):
                e[k] = f[f"espn_{k}"]
        src["espn"] = e
    if ml_pred is not None:
        src["ml"] = ml_pred
    return src


def blend(src, weights, target):
    num = den = 0.0
    for s, vals in src.items():
        if target in vals and not math.isnan(vals[target]):
            wt = weights.get(target, {}).get(s, 0.0)
            num += wt * vals[target]
            den += wt
    if den == 0:
        return src["sps"][target]
    return num / den


def fit_convex_weights(records, target, sources=("sps", "espn", "ml")):
    """Minimise weighted squared error over convex combinations of the sources."""
    have = [s for s in sources
            if sum(1 for r in records if target in r["y"] and target in r["src"].get(s, {})) >= 100]
    if len(have) < len(sources) and have:
        return {**{s: 0.0 for s in sources}, **fit_convex_weights(records, target, tuple(have))}
    data = [(r["src"], r["y"][target], r["w"][target]) for r in records
            if target in r["y"] and all(target in r["src"].get(s, {}) for s in sources)]
    if len(data) < 50:
        return {s: 1.0 / len(sources) for s in sources}
    P = np.array([[d[0][s][target] for s in sources] for d in data])
    y = np.array([d[1] for d in data])
    w = np.array([d[2] for d in data])

    def loss(z):
        a = np.exp(z) / np.exp(z).sum()
        return float(np.sum(w * (P @ a - y) ** 2))

    res = minimize(loss, np.zeros(len(sources)), method="Nelder-Mead", options={"maxiter": 4000, "xatol": 1e-5})
    a = np.exp(res.x) / np.exp(res.x).sum()
    return {s: float(round(v, 4)) for s, v in zip(sources, a)}


def audit_espn(ds):
    """Flag seasons whose stored ESPN 'projection' is not a preseason projection.

    ESPN keeps one projection per season; for some seasons it was refreshed in-season (projected
    games ~ rest-of-season, or errors far below every other season). Using those would leak
    in-season information into training and flatter ESPN in the backtest.
    """
    per = {}
    for (pid, season), e in ds.espn.items():
        line = next((h for h in ds.lines.get(pid, []) if h.season == season), None)
        rec = per.setdefault(season, {"gp": [], "err": []})
        rec["gp"].append(e.gp)
        if line and line.gp >= 20:
            actual = SCORING.fantasy_points({k: v / line.gp for k, v in line.stats.items()})
            rec["err"].append(abs(SCORING.fantasy_points(e.per_game) - actual))
    audit = {s: {"mean_gp": round(float(np.mean(v["gp"])), 1),
                 "fppg_mae": round(float(np.mean(v["err"])), 2) if v["err"] else None}
             for s, v in sorted(per.items())}
    maes = [a["fppg_mae"] for s, a in audit.items() if a["fppg_mae"] is not None and s < 2027]
    med = float(np.median(maes)) if maes else 0.0
    excluded = [s for s, a in audit.items() if s < 2027 and (
        a["mean_gp"] < 0.65 * season_games(s) or (a["fppg_mae"] is not None and a["fppg_mae"] < 0.75 * med))]
    return excluded, audit


def estimate_dispersion(path):
    """Overdispersion of per-game counts vs. Poisson, pooled over players (phi = 1 is Poisson)."""
    if not path.exists():
        return {k: 1.0 for k in RATE_STATS}, None
    logs = json.loads(path.read_text())
    num = {k: 0.0 for k in RATE_STATS}
    den = {k: 0.0 for k in RATE_STATS}
    cvs = []
    for games in logs.values():
        n = len(games)
        mins = np.array([g["minutes"] for g in games])
        tot = [line_rates_game(g["stats"]) for g in games]
        for k in RATE_STATS:
            x = np.array([t[k] for t in tot])
            lam = x.sum() / mins.sum()
            exp = lam * mins
            if exp.sum() <= 0:
                continue
            num[k] += float(((x - exp) ** 2).sum()) * n / (n - 1)
            den[k] += float(exp.sum())
        fps = np.array([SCORING.fantasy_points(g["stats"]) for g in games])
        if fps.mean() > 10:
            cvs.append(fps.std(ddof=1) / fps.mean())
    phi = {k: float(max(1.0, num[k] / den[k])) if den[k] else 1.0 for k in RATE_STATS}
    return phi, float(np.median(cvs)) if cvs else None


def line_rates_game(stats):
    from fbb.models import SeasonLine
    return line_rates(SeasonLine(season=0, stats=stats))


def fp_pg(rates36: dict[str, float], mpg: float) -> float:
    rates = {k: v / 36 for k, v in rates36.items()}
    return SCORING.fantasy_points(per_game_from_rates(rates, mpg))


def _metrics(pop, season, methods):
    actual_fppg = np.array([SCORING.fantasy_points({k: v / r["actual"].gp for k, v in r["actual"].stats.items()})
                            for r in pop])
    actual_tot = np.array([a * r["actual"].gp for a, r in zip(actual_fppg, pop)])
    G = season_games(season)
    out = {}
    for name in methods:
        pg, tot = [], []
        for r in pop:
            if name == "naive":
                l1 = next((h for h in r["hist"] if h.season == season - 1), None)
                if l1 is None or l1.gp == 0:
                    v, g = r["fp"]["sps"], r["src"]["sps"]["avail"]
                else:
                    v = SCORING.fantasy_points({k: x / l1.gp for k, x in l1.stats.items()})
                    g = min(1.0, l1.gp / season_games(season - 1))
            elif name == "espn":
                v = SCORING.fantasy_points(r["espn"].per_game)
                g = min(1.0, r["espn"].gp / 82)
            else:
                v, g = r["fp"][name], r["avail"][name]
            pg.append(v)
            tot.append(v * g * G)
        pg, tot = np.array(pg), np.array(tot)
        out[name] = {
            "fppg_mae": float(np.mean(np.abs(pg - actual_fppg))),
            "fppg_rmse": float(np.sqrt(np.mean((pg - actual_fppg) ** 2))),
            "total_mae": float(np.mean(np.abs(tot - actual_tot))),
            "total_spearman": float(spearmanr(tot, actual_tot).correlation),
            "fppg_spearman": float(spearmanr(pg, actual_fppg).correlation),
        }
    return out


def evaluate(records, season):
    """Errors on one test season.

    vs_espn: players with 20+ games and a genuine ESPN preseason projection (all five methods).
    all:     every player with 20+ games (methods that don't need ESPN).
    """
    pop_all = [r for r in records if r["season"] == season and r["actual"] is not None and r["actual"].gp >= 20]
    pop_espn = [r for r in pop_all if r["espn"] is not None]
    out = {"n_players": len(pop_espn), "n_all": len(pop_all)}
    if len(pop_espn) >= 50:
        out["methods"] = _metrics(pop_espn, season, ("naive", "sps", "espn", "ml", "ensemble"))
    out["methods_all"] = _metrics(pop_all, season, ("naive", "sps", "ml", "ensemble"))
    return out


def main():
    t0 = time.time()
    ds = load_history()
    if not ds.lines:
        sys.exit("No history found. Run: python -m scripts.build_history")
    lg = league_rates([ln for ln in ds.all_lines() if ln.season < 2027])
    excluded_espn, audit = audit_espn(ds)
    for (pid, season) in [k for k in ds.espn if k[1] in excluded_espn]:
        del ds.espn[(pid, season)]
    print("ESPN projection audit:", json.dumps(audit))
    print("Excluded ESPN seasons (not genuinely preseason):", excluded_espn, "| ADP unavailable:", ds.adp_missing_seasons)
    rows, _ = build_rows(ds, lg)
    for r in rows:
        r["hist"] = ds.lines.get(r["pid"], [])
    train_pool = [r for r in rows if r["season"] <= 2026]
    print(f"{len(train_pool)} player-season rows, {time.time() - t0:.1f}s")

    # ---- rolling-origin backtest -----------------------------------------
    oof = []
    for T in BACKTEST_SEASONS:
        tr = [r for r in train_pool if r["season"] < T]
        te = [r for r in train_pool if r["season"] == T]
        boosters = {t: fit_target(tr, t) for t in TARGETS}
        preds = predict_rows(boosters, te)
        for i, r in enumerate(te):
            ml = {t: float(preds[t][i]) for t in TARGETS}
            ml["avail"] = float(np.clip(ml["avail"], 0.05, 1.0))
            ml["mpg"] = float(np.clip(ml["mpg"], 3.0, 40.0))
            rec = dict(r)
            rec["src"] = source_values(r, ml)
            oof.append(rec)
        print(f"  backtest fold {T}: train={len(tr)} test={len(te)}")

    # Ensemble weights for season T come only from earlier folds (no peeking).
    for T in REPORT_SEASONS:
        prev = [r for r in oof if r["season"] < T]
        weights = {t: fit_convex_weights(prev, t) for t in TARGETS}
        for r in oof:
            if r["season"] != T:
                continue
            ens = {t: blend(r["src"], weights, t) for t in TARGETS}
            r["fp"] = {
                "sps": fp_pg({k: r["src"]["sps"][k] for k in RATE_STATS}, r["src"]["sps"]["mpg"]),
                "ml": fp_pg({k: r["src"]["ml"][k] for k in RATE_STATS}, r["src"]["ml"]["mpg"]),
                "ensemble": fp_pg({k: ens[k] for k in RATE_STATS}, ens["mpg"]),
            }
            r["avail"] = {"sps": r["src"]["sps"]["avail"], "ml": r["src"]["ml"]["avail"], "ensemble": ens["avail"]}
            r["ens"] = ens

    report = {T: evaluate([r for r in oof if r["season"] == T], T) for T in REPORT_SEASONS}
    keys = ("fppg_mae", "fppg_rmse", "total_mae", "total_spearman", "fppg_spearman")
    espn_seasons = [T for T in REPORT_SEASONS if "methods" in report[T]]
    summary = {m: {k: float(np.mean([report[T]["methods"][m][k] for T in espn_seasons])) for k in keys}
               for m in ("naive", "sps", "espn", "ml", "ensemble")} if espn_seasons else {}
    summary_all = {m: {k: float(np.mean([report[T]["methods_all"][m][k] for T in REPORT_SEASONS])) for k in keys}
                   for m in ("naive", "sps", "ml", "ensemble")}
    for title, table, seasons in (("vs ESPN (seasons with genuine preseason projections)", summary, espn_seasons),
                                  ("all players with 20+ games", summary_all, REPORT_SEASONS)):
        print(f"\nBacktest {title}: {seasons}")
        print(f"{'method':<10}{'FP/G MAE':>10}{'FP/G RMSE':>11}{'Total MAE':>11}{'Total rho':>11}")
        for m, v in table.items():
            print(f"{m:<10}{v['fppg_mae']:>10.2f}{v['fppg_rmse']:>11.2f}{v['total_mae']:>11.0f}{v['total_spearman']:>11.3f}")

    # ---- final fit ---------------------------------------------------------
    final_weights = {t: fit_convex_weights(oof, t) for t in TARGETS}
    # In-season prior strength (minutes of evidence the preseason projection is "worth"):
    # Gamma-Poisson: K = mean_rate / Var(true_rate - projected_rate), with the Poisson
    # sampling noise removed from the observed error variance (method of moments).
    phi, fp_cv = estimate_dispersion(ROOT / "data" / "history" / "game_logs_2026.json")
    print("Overdispersion (phi):", json.dumps({k: round(v, 2) for k, v in phi.items()}), "FP game CV:", fp_cv)
    prior_minutes = {}
    w_split = float(np.median([r["feats"]["w_min"] for r in oof if "ens" in r and r["feats"]["w_min"] > 0]))
    for k in RATE_STATS:
        buckets = {}
        for name, keep in (("low", lambda wm: wm < w_split), ("high", lambda wm: wm >= w_split)):
            errs, noise, means = [], [], []
            for r in oof:
                if ("ens" not in r or k not in r["y"] or r["actual"] is None or r["actual"].minutes < 600
                        or not keep(r["feats"]["w_min"])):
                    continue
                obs = r["y"][k] / 36
                proj = r["ens"][k] / 36
                errs.append(obs - proj)
                noise.append(phi[k] * max(obs, 1e-6) / r["actual"].minutes)
                means.append(obs)
            true_var = max(np.var(errs) - np.mean(noise), 1e-9)
            buckets[name] = float(np.clip(np.mean(means) / true_var, 150.0, 8000.0))
        prior_minutes[k] = {"low": buckets["low"], "high": buckets["high"], "split": w_split}
    boosters = {t: fit_target(train_pool, t) for t in TARGETS}
    importance = {}
    for t, b in boosters.items():
        gain = b.feature_importance("gain")
        top = np.argsort(gain)[::-1][:6]
        importance[t] = [(feature_names()[i], float(gain[i] / gain.sum())) for i in top]

    OUT.mkdir(parents=True, exist_ok=True)
    metrics = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "trained_through": 2026,
        "train_rows": len(train_pool),
        "data_audit": {"excluded_espn_seasons": excluded_espn, "espn_by_season": audit,
                       "adp_missing_seasons": ds.adp_missing_seasons},
        "scoring": "ESPN default H2H points",
        "report_seasons": REPORT_SEASONS,
        "espn_report_seasons": espn_seasons,
        "summary": summary,
        "summary_all": summary_all,
        "by_season": {str(k): v for k, v in report.items()},
        "ensemble_weights": final_weights,
        "prior_minutes": prior_minutes,
        "overdispersion": phi,
        "fp_game_cv": fp_cv,
        "feature_importance": importance,
        "lgb_params": {**LGB_PARAMS, "num_boost_round": N_ROUNDS},
    }
    bundle = {
        "boosters": boosters,
        "features": feature_names(),
        "ensemble": final_weights,
        "prior_minutes": prior_minutes,
        "fp_game_cv": fp_cv,
        "metrics": metrics,
        "trained_through": 2026,
    }
    joblib.dump(bundle, OUT / "projection_model.joblib")
    (OUT / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print("\nEnsemble weights:", json.dumps(final_weights))
    print("Prior minutes:", json.dumps({k: (round(v["low"]), round(v["high"])) for k, v in prior_minutes.items()}),
          "split at weighted minutes", round(w_split))
    print(f"saved -> {OUT}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
