"""The models: who wins, by how much, and how many points, with a backtest against simple forecasts.

Home win probability comes from two models averaged: a logistic regression (smooth, hard to overfit) and
XGBoost (finds interactions, such as rest mattering more for a short-handed team). Two XGBoost regressions
predict the home margin and the total points, which give the predicted score.

Backtest: rolling origin. Each test season is predicted by models trained only on seasons before it, and
compared with always picking the home team, the Elo ratings alone, a random forest, and each model on its own.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import NormalDist

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURES, GROUPS, elo_probability

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "game_model.joblib"
METRICS_PATH = ROOT / "models" / "metrics.json"
FIRST_TRAIN_SEASON = 2015  # 2014-15 only warms up the ratings
MIN_TRAIN_SEASONS = 4
XGB = dict(n_estimators=350, max_depth=3, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
           min_child_weight=8, reg_lambda=8.0, n_jobs=4, random_state=7)
NORMAL = NormalDist()


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def fit(train: pd.DataFrame, with_forest: bool = False) -> dict:
    """Every model, fitted on ``train`` (rows from features.build)."""
    X = train[FEATURES].to_numpy(float)
    y = train["home_win"].to_numpy(int)
    models = {
        "lr": make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=3000)).fit(X, y),
        "xgb": xgb.XGBClassifier(**XGB, eval_metric="logloss").fit(X, y),
        "margin": xgb.XGBRegressor(**XGB).fit(X, train["margin"].to_numpy(float)),
        "total": xgb.XGBRegressor(**XGB).fit(X, train["total"].to_numpy(float)),
        "home_rate": float(y.mean()),
    }
    if with_forest:
        models["rf"] = RandomForestClassifier(n_estimators=300, min_samples_leaf=25, n_jobs=4, random_state=7).fit(X, y)
    return models


def predict(models: dict, X: np.ndarray) -> dict[str, np.ndarray]:
    """Home win probability from each model and the two averaged ("p"), and the predicted margin and total."""
    lr = models["lr"].predict_proba(X)[:, 1]
    gb = models["xgb"].predict_proba(X)[:, 1]
    out = {"lr": lr, "xgb": gb, "p": 0.5 * (lr + gb), "margin": models["margin"].predict(X),
           "total": models["total"].predict(X)}
    if "rf" in models:
        out["rf"] = models["rf"].predict_proba(X)[:, 1]
    return out


def score(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {"accuracy": float(((p > 0.5) == (y == 1)).mean()),
            "log_loss": float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
            "brier": float(((p - y) ** 2).mean()),
            "auc": float(roc_auc_score(y, p)) if 0 < y.mean() < 1 and np.ptp(p) > 0 else float("nan")}


def calibration(y: np.ndarray, p: np.ndarray, edges=(0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)) -> list[dict]:
    rows = []
    for lo, hi in zip(edges, edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() >= 20:
            rows.append({"range": f"{lo:.0%}-{hi:.0%}", "games": int(m.sum()), "predicted": float(p[m].mean()),
                         "home_won": float(y[m].mean())})
    return rows


def backtest(games: pd.DataFrame, test_seasons: list[int], progress=None) -> dict:
    """Each test season predicted from the seasons before it. Returns per-season and overall scores for every
    method, the spread of the score predictions' misses, and the out-of-sample predictions themselves."""
    games = games[games["season"] >= FIRST_TRAIN_SEASON]
    parts = []
    by_season = {}
    for T in (int(s) for s in test_seasons):
        train, test = games[games["season"] < T], games[games["season"] == T]
        if train["season"].nunique() < MIN_TRAIN_SEASONS or test.empty:
            continue
        models = fit(train, with_forest=True)
        X, y = test[FEATURES].to_numpy(float), test["home_win"].to_numpy(int)
        pred = predict(models, X)
        sigma = float(np.std(train["margin"] - predict(models, train[FEATURES].to_numpy(float))["margin"]))
        methods = {
            "home": np.full(len(y), models["home_rate"]),
            "elo": elo_probability(test["elo_diff"].to_numpy(float), test["neutral"].to_numpy(float)),
            "logistic": pred["lr"], "random_forest": pred["rf"], "xgboost": pred["xgb"], "blend": pred["p"],
            # The margin model read as a probability; its in-sample spread is a little tight, so widen it.
            "margin_model": np.array([NORMAL.cdf(m / (sigma * 1.08)) for m in pred["margin"]]),
        }
        rec = {"games": int(len(y)), **{name: score(y, p) for name, p in methods.items()},
               "margin_mae": float(np.abs(pred["margin"] - test["margin"]).mean()),
               "total_mae": float(np.abs(pred["total"] - test["total"]).mean()),
               "margin_mae_naive": float(np.abs(train["margin"].mean() - test["margin"]).mean()),
               "total_mae_naive": float(np.abs(train[train["season"] == train["season"].max()]["total"].mean()
                                               - test["total"]).mean())}
        by_season[T] = rec
        part = test[["game_id", "season", "date", "home", "away", "home_win", "margin", "total"]].copy()
        for name, p in methods.items():
            part[f"p_{name}"] = p
        part["pred_margin"], part["pred_total"] = pred["margin"], pred["total"]
        parts.append(part)
        if progress:
            progress(f"{T}-{(T + 1) % 100:02d}: {len(train):,} games to train, {len(y):,} to test, "
                     f"blend {rec['blend']['accuracy']:.1%} / log loss {rec['blend']['log_loss']:.4f}")
    oof = pd.concat(parts, ignore_index=True)
    y = oof["home_win"].to_numpy(int)
    overall = {name[2:]: score(y, oof[name].to_numpy(float)) for name in oof.columns if name.startswith("p_")}
    miss_m, miss_t = oof["margin"] - oof["pred_margin"], oof["total"] - oof["pred_total"]
    return {
        "test_seasons": sorted(by_season), "games": int(len(oof)), "overall": overall,
        "by_season": {str(k): v for k, v in by_season.items()},
        "margin_mae": float(miss_m.abs().mean()), "total_mae": float(miss_t.abs().mean()),
        "margin_mae_naive": float(np.mean([v["margin_mae_naive"] for v in by_season.values()])),
        "total_mae_naive": float(np.mean([v["total_mae_naive"] for v in by_season.values()])),
        "sigma_margin": float(miss_m.std()), "sigma_total": float(miss_t.std()),
        "rho": float(np.corrcoef(miss_m, miss_t)[0, 1]),
        "calibration": calibration(y, oof["p_blend"].to_numpy(float)),
        "oof": oof,
    }


def train(games: pd.DataFrame, test_seasons: list[int], progress=None) -> dict:
    """Backtest, then fit on every game and save the models with what the backtest measured."""
    bt = backtest(games, test_seasons, progress)
    oof = bt.pop("oof")
    final = games[games["season"] >= FIRST_TRAIN_SEASON]
    models = fit(final)
    importance = sorted(zip(FEATURES, (float(x) for x in models["xgb"].feature_importances_)), key=lambda kv: -kv[1])
    metrics = {"trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "trained_through": str(final["date"].max()),
               "train_games": int(len(final)), "seasons": [int(final["season"].min()), int(final["season"].max())],
               "features": FEATURES, "backtest": bt, "feature_importance": importance[:12],
               "xgb_params": {k: v for k, v in XGB.items() if k != "n_jobs"}}
    bundle = {"models": models, "features": FEATURES, "sigma_margin": bt["sigma_margin"], "sigma_total": bt["sigma_total"],
              "rho": bt["rho"], "metrics": metrics}
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2))
    bundle["oof"] = oof
    return bundle


def load(path: Path | None = None) -> dict:
    path = path or MODEL_PATH
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}. Run: python -m gamepred train")
    bundle = joblib.load(path)
    if bundle.get("features") != FEATURES:
        raise RuntimeError("The saved model was trained on different features. Run: python -m gamepred train")
    return bundle


def factors(models: dict, row: dict[str, float]) -> list[dict]:
    """What moves this game's home win probability, in percentage points, by kind of evidence.

    Both models add up evidence on the log-odds scale: the regression's terms and XGBoost's TreeSHAP
    contributions. A factor's effect is the probability with it, minus the probability with its evidence taken
    out of both models. Home court is the starting point: what the models say with no evidence either way.
    The effects are a reading of the models, not exact pieces that add up to the probability.
    """
    X = np.array([[row[f] for f in FEATURES]], dtype=float)
    scaler, lr = models["lr"].steps[0][1], models["lr"].steps[1][1]
    lr_terms = lr.coef_[0] * scaler.transform(X)[0]
    lr_logit = float(lr.intercept_[0] + lr_terms.sum())
    contrib = models["xgb"].get_booster().predict(xgb.DMatrix(X, feature_names=FEATURES), pred_contribs=True)[0]
    gb_terms, gb_logit = contrib[:-1], float(contrib.sum())
    p = 0.5 * (sigmoid(lr_logit) + sigmoid(gb_logit))
    index = {f: i for i, f in enumerate(FEATURES)}
    out = [{"factor": "Home court", "points": float(100 * (0.5 * (sigmoid(lr.intercept_[0]) + sigmoid(contrib[-1])) - 0.5))}]
    for group, names in GROUPS.items():
        ids = [index[f] for f in names]
        without = 0.5 * (sigmoid(lr_logit - lr_terms[ids].sum()) + sigmoid(gb_logit - gb_terms[ids].sum()))
        out.append({"factor": group, "points": float(100 * (p - without))})
    return sorted((f for f in out if abs(f["points"]) >= 0.25), key=lambda f: -abs(f["points"]))
