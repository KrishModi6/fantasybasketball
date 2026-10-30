"""Expected fantasy points (xFP): what a player's chances were worth, apart from how many went in.

NFL Next Gen Stats, F1 Insights and the NBA's own xFG% (all built on AWS) share one idea: tracking data
describes every opportunity, a model prices it, and actual minus expected is luck. Here the opportunities
come from NBA.com (connectors/nba_stats.py) and each is priced at the player's established rate:

  FGM, 3PM  every shot, priced by a shot model (distance, court location, shot type, period and clock) for
            a league-average shooter, plus his established shot-making above that
  FTM       free-throw attempts x his established FT%
  AST       potential assists (passes that led to a shot) x his established conversion
  REB       rebound chances (within 3.5 ft of the ball) x his established conversion
  STL       deflections x his established steals per deflection
  BLK       contested 2-pointers x his established blocks per contest

"Established" is his last two seasons (the older at half weight) shrunk toward the league rate by
PSEUDO_COUNTS opportunities, so a rookie converts at the league rate.

A TrackingLine holds a player's season-to-date box score twice: as it happened and with each stat above
replaced by its expected value. The projection engine moves this season's evidence part of the way from
actual toward expected (BETA per stat, learned in scripts/backtest_xfp.py) before its in-season update.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np

from .history import DATA_DIR
from .models import ScoringSettings, TrackingLine

log = logging.getLogger(__name__)
MODEL_PATH = DATA_DIR / "model" / "xfp_model.joblib"

# stat -> (opportunity, conversion) columns of the NBA.com dashboards; shooting is handled by the shot model.
COMPONENTS = {
    "FTM": ("FTA", "FTM"),
    "AST": ("POTENTIAL_AST", "AST"),
    "REB": ("REB_CHANCES", "REB"),
    "STL": ("DEFLECTIONS", "STL"),
    "BLK": ("CONTESTED_SHOTS_2PT", "BLK"),
}
# Opportunities of league-average conversion added to a player's own history before taking his rate.
PSEUDO_COUNTS = {"FG": 400.0, "3P": 300.0, "FTM": 150.0, "AST": 200.0, "REB": 300.0, "STL": 120.0, "BLK": 300.0}
PRIOR_SEASON_WEIGHTS = (1.0, 0.5)  # last season, two seasons ago
# Engine rate stats moved by each xFP stat (makes and misses move together, so attempts stay put).
BETA_GROUPS = {"FG": ("FGM", "FGMI"), "3P": ("3PM", "3PMI"), "FTM": ("FTM", "FTMI"),
               "AST": ("AST",), "REB": ("REB",), "STL": ("STL",), "BLK": ("BLK",)}

# ---- shot model ------------------------------------------------------------

# Compact shot rows (scripts/fetch_tracking.py):
# [yyyymmdd, player, game, period, seconds left in period, action, is 3, zone, feet, x, y, made]
SHOT_FEATURES = ["feet", "x", "y", "is3", "period", "secs_left", "action", "zone"]
SHOT_PARAMS = dict(objective="binary", learning_rate=0.08, num_leaves=31, min_child_samples=200,
                   cat_smooth=20, verbose=-1, seed=7)
SHOT_ROUNDS = 300


class ShotModel:
    """P(make) for a league-average shooter, from where and how the shot was taken."""

    def __init__(self, booster: lgb.Booster | None = None, actions: list[str] | None = None,
                 zones: list[str] | None = None):
        self.booster = booster
        self.actions = actions or []
        self.zones = zones or []

    def _matrix(self, shots: list[list]) -> np.ndarray:
        a = {v: i for i, v in enumerate(self.actions)}
        z = {v: i for i, v in enumerate(self.zones)}
        return np.array([[s[8], s[9], s[10], s[6], min(s[3], 5), s[4], a.get(s[5], -1), z.get(s[7], -1)]
                         for s in shots], dtype=float)

    def fit(self, shots: list[list]) -> "ShotModel":
        counts: dict[str, int] = defaultdict(int)
        for s in shots:
            counts[s[5]] += 1
        self.actions = sorted(k for k, n in counts.items() if n >= 50)  # rare actions share "unknown"
        self.zones = sorted({s[7] for s in shots})
        data = lgb.Dataset(self._matrix(shots), np.array([s[11] for s in shots]), feature_name=SHOT_FEATURES,
                           categorical_feature=["action", "zone"], free_raw_data=False)
        self.booster = lgb.train(SHOT_PARAMS, data, num_boost_round=SHOT_ROUNDS)
        return self

    def predict(self, shots: list[list]) -> np.ndarray:
        if not shots:
            return np.zeros(0)
        return self.booster.predict(self._matrix(shots))

    def to_dict(self) -> dict:
        return {"booster": self.booster.model_to_string(), "actions": self.actions, "zones": self.zones}

    @classmethod
    def from_dict(cls, d: dict) -> "ShotModel":
        return cls(lgb.Booster(model_str=d["booster"]), d["actions"], d["zones"])


def shot_totals(shots: list[list], probs: np.ndarray, through: int | None = None) -> dict[int, dict[str, float]]:
    """Per player: attempts, makes and expected makes (all shots and threes), through a yyyymmdd date."""
    out: dict[int, dict[str, float]] = defaultdict(lambda: {"FGA": 0.0, "FGM": 0.0, "xFG": 0.0,
                                                             "3PA": 0.0, "3PM": 0.0, "x3P": 0.0})
    for s, p in zip(shots, probs):
        if through is not None and s[0] > through:
            continue
        t = out[s[1]]
        t["FGA"] += 1
        t["FGM"] += s[11]
        t["xFG"] += p
        if s[6]:
            t["3PA"] += 1
            t["3PM"] += s[11]
            t["x3P"] += p
    return dict(out)


# ---- established conversion rates ------------------------------------------

def league_rates(dash: dict[int, dict]) -> dict[str, float]:
    """League conversion per opportunity for each component, from one season's dashboards."""
    out = {}
    for stat, (opp, made) in COMPONENTS.items():
        n = sum(r.get(opp, 0) for r in dash.values())
        out[stat] = sum(r.get(made, 0) for r in dash.values() if r.get(opp, 0) > 0) / n if n else 0.0
    return out


def skills(priors: list[tuple[dict | None, dict | None]], lg: dict[str, float]) -> dict[str, float]:
    """A player's established rates from up to two earlier seasons of (dashboard row, shot totals).

    Shooting skill is makes above the shot model per attempt (0 = league-average shot-maker); the other
    components are conversion per opportunity.
    """
    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    for w, (row, shots) in zip(PRIOR_SEASON_WEIGHTS, priors):
        if shots:
            num["FG"] += w * (shots["FGM"] - shots["xFG"])
            den["FG"] += w * shots["FGA"]
            num["3P"] += w * (shots["3PM"] - shots["x3P"])
            den["3P"] += w * shots["3PA"]
        if row:
            for stat, (opp, made) in COMPONENTS.items():
                if row.get(opp, 0) > 0:
                    num[stat] += w * row.get(made, 0)
                    den[stat] += w * row[opp]
    out = {g: num[g] / (den[g] + PSEUDO_COUNTS[g]) for g in ("FG", "3P")}
    for stat in COMPONENTS:
        out[stat] = (num[stat] + PSEUDO_COUNTS[stat] * lg[stat]) / (den[stat] + PSEUDO_COUNTS[stat])
    return out


def tracking_line(season: int, row: dict, shots: dict[str, float] | None, skill: dict[str, float]) -> TrackingLine:
    """Season-to-date box score as it happened and as expected from the player's opportunities."""
    actual = {
        "PTS": 2 * (row["FGM"] - row["FG3M"]) + 3 * row["FG3M"] + row["FTM"],
        "FGM": row["FGM"], "FGA": row["FGA"], "3PM": row["FG3M"], "3PA": row["FG3A"], "FTM": row["FTM"],
        "FTA": row["FTA"], "REB": row["REB"], "AST": row["AST"], "STL": row["STL"], "BLK": row["BLK"],
        "TO": row["TOV"],
    }
    exp = dict(actual)
    if shots and shots["FGA"]:
        # The shot feed and the box score can disagree by a shot or two; scale to the box score's attempts.
        exp["FGM"] = min(row["FGA"], (shots["xFG"] / shots["FGA"] + skill["FG"]) * row["FGA"])
        exp["3PM"] = min(row["FG3A"], (shots["x3P"] / shots["3PA"] + skill["3P"]) * row["FG3A"]) if shots["3PA"] else 0.0
    for stat, (opp, _) in COMPONENTS.items():
        if stat == "FTM" or row.get(opp, 0) > 0:
            exp[stat] = row.get(opp, 0) * skill[stat]
    exp["PTS"] = 2 * (exp["FGM"] - exp["3PM"]) + 3 * exp["3PM"] + exp["FTM"]
    return TrackingLine(season=season, gp=row["GP"], minutes=row["MIN"], actual=actual, expected=exp)


def xfp_per_game(line: TrackingLine, scoring: ScoringSettings) -> tuple[float, float]:
    """(actual, expected) fantasy points per game over the games the line covers."""
    if not line.gp:
        return 0.0, 0.0
    return scoring.fantasy_points(line.actual) / line.gp, scoring.fantasy_points(line.expected) / line.gp


# ---- trained bundle ----------------------------------------------------------

class XfpModel:
    """The shot model, league rates and per-stat blend weights saved by scripts/backtest_xfp.py."""

    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.shot_model = ShotModel.from_dict(bundle["shot_model"])
        self.beta: dict[str, float] = bundle["beta"]
        self.league: dict[str, float] = bundle["league"]

    def rate_beta(self) -> dict[str, float]:
        """BETA expanded to the engine's rate stats."""
        return {k: b for g, b in self.beta.items() for k in BETA_GROUPS[g]}

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls, path: str | None = None) -> "XfpModel | None":
        p = Path(path) if path else MODEL_PATH
        if not p.exists():
            return None
        try:
            return cls(joblib.load(p))
        except Exception as exc:  # noqa: BLE001 - a broken bundle must not stop projections
            log.warning("could not load xFP model: %s", exc)
            return None


def save_bundle(shot_model: ShotModel, beta: dict[str, float], league: dict[str, float], metrics: dict) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"shot_model": shot_model.to_dict(), "beta": beta, "league": league,
                 "pseudo_counts": PSEUDO_COUNTS, "metrics": metrics}, MODEL_PATH)
    (MODEL_PATH.parent / "xfp_metrics.json").write_text(json.dumps(
        {**metrics, "beta": beta, "league": league, "pseudo_counts": PSEUDO_COUNTS}, indent=2))
