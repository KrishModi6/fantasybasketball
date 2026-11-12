"""Breakout candidates: how likely each player is to have a breakout fantasy season.

A breakout season is one where a player's fantasy points per game rise by at least MIN_GAIN over his last
season, to at least MIN_LEVEL, over at least MIN_GAMES games (ESPN default points). About one qualifying
player in nine has one each year.

The model is a LightGBM classifier (scripts/train_breakout.py) over what is known before the season starts:

  minutes       last season's minutes, the change from the season before, and the minutes ESPN and the
                stat baseline project
  usage         shots, free throws and turnovers used per 36 minutes, and their change
  efficiency    true shooting and fantasy points per minute, and their change
  development   age, years in the league, and how far last season fell short of his best
  projections   the fantasy points ESPN and the stat baseline (fbb/baseline.py) project against last season

It reads the bundled season history (data/history), not a league, so the probabilities are the same in every
league and don't move during the season; the app shows this season's games beside them.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np

from .baseline import league_rates, per_game_from_rates, sps_projection
from .constants import season_games
from .history import DATA_DIR, HistoryDataset, load_history
from .models import ExpertProjection, LeagueSnapshot, ScoringSettings, SeasonLine

log = logging.getLogger(__name__)
MODEL_PATH = DATA_DIR / "model" / "breakout_model.joblib"

MIN_PRIOR_GAMES = 20  # games last season for a player to have a level to break out from
MIN_GAMES = 30  # games in the breakout season
MIN_GAIN = 5.0  # fantasy points per game gained
MIN_LEVEL = 20.0  # fantasy points per game reached: the bench of a 10-team league starts about here
SCORING = ScoringSettings()  # ESPN default points
NAN = float("nan")

FEATURES = [
    "age", "years",
    "l1_gp_frac", "l1_mpg", "l1_fp_pg", "l1_fp36", "l1_usage", "l1_ts", "l1_ast36", "l1_reb36", "l1_stocks36", "l1_3pm36",
    "l2_mpg", "l2_fp_pg", "l2_fp36", "l2_usage", "l2_ts",
    "d_mpg", "d_fp36", "d_usage", "d_ts",
    "peak_gap", "peak_mpg_gap",
    "sps_gain", "sps_mpg_gain",
    "espn_gain", "espn_mpg_gain", "espn_gp_frac",
]
# What each feature is evidence of, for the reasons shown beside a probability.
GROUPS = {
    "Age and experience": ("age", "years"),
    "Minutes": ("l1_mpg", "l2_mpg", "d_mpg", "l1_gp_frac", "peak_mpg_gap", "sps_mpg_gain"),
    "Usage": ("l1_usage", "l2_usage", "d_usage"),
    "Efficiency": ("l1_ts", "l2_ts", "d_ts", "l1_fp36", "l2_fp36", "d_fp36"),
    "Production so far": ("l1_fp_pg", "l2_fp_pg", "peak_gap", "l1_ast36", "l1_reb36", "l1_stocks36", "l1_3pm36", "sps_gain"),
    "ESPN's projection": ("espn_gain", "espn_mpg_gain", "espn_gp_frac"),
}
_GROUP_OF = {f: g for g, fs in GROUPS.items() for f in fs}


def fp_per_game(line: SeasonLine) -> float:
    return SCORING.fantasy_points({k: v / line.gp for k, v in line.stats.items()}) if line.gp else 0.0


def is_breakout(last: SeasonLine | None, season: SeasonLine | None) -> bool | None:
    """Whether ``season`` was a breakout from ``last``. None when ``last`` gives no level to break out from."""
    if last is None or last.gp < MIN_PRIOR_GAMES:
        return None
    if season is None or season.gp < MIN_GAMES:
        return False
    now = fp_per_game(season)
    return now >= MIN_LEVEL and now - fp_per_game(last) >= MIN_GAIN


def _shape(line: SeasonLine) -> dict[str, float]:
    """Per-game and per-36 measures of one season."""
    s, m = line.stats, line.minutes
    if not line.gp or m <= 0:
        return {}
    per36 = lambda x: x / m * 36  # noqa: E731
    used = s.get("FGA", 0.0) + 0.44 * s.get("FTA", 0.0)
    fp = fp_per_game(line)
    return {
        "mpg": m / line.gp, "fp_pg": fp, "fp36": fp * line.gp / m * 36,
        "usage": per36(used + s.get("TO", 0.0)),
        "ts": s.get("PTS", 0.0) / (2 * used) if used > 0 else NAN,
        "ast36": per36(s.get("AST", 0.0)), "reb36": per36(s.get("REB", 0.0)),
        "stocks36": per36(s.get("STL", 0.0) + s.get("BLK", 0.0)), "3pm36": per36(s.get("3PM", 0.0)),
    }


def build_features(history: list[SeasonLine], season: int, age: float | None, first_season: int | None,
                   espn: ExpertProjection | None, lg: dict[int, dict[str, float]]) -> dict[str, float] | None:
    """Preseason features for ``season`` from a player's earlier seasons. None when he didn't play
    MIN_PRIOR_GAMES games last season: there's no level to break out from."""
    prior = {h.season: h for h in history if h.season < season and h.minutes > 0}
    l1 = prior.get(season - 1)
    if l1 is None or l1.gp < MIN_PRIOR_GAMES:
        return None
    a, b = _shape(l1), _shape(prior[season - 2]) if season - 2 in prior else {}
    f: dict[str, float] = {
        "age": age if age is not None else NAN,
        "years": float(season - first_season + 1) if first_season else NAN,
        "l1_gp_frac": l1.gp / season_games(l1.season),
    }
    for k in ("mpg", "fp_pg", "fp36", "usage", "ts", "ast36", "reb36", "stocks36", "3pm36"):
        f[f"l1_{k}"] = a[k]
    for k in ("mpg", "fp_pg", "fp36", "usage", "ts"):
        f[f"l2_{k}"] = b.get(k, NAN)
        if k != "fp_pg":
            f[f"d_{k}"] = a[k] - b[k] if k in b else NAN
    # A down year after a better one: how far last season fell short of his best before it.
    earlier = [_shape(h) for s, h in prior.items() if s < season - 1 and h.gp >= MIN_PRIOR_GAMES]
    f["peak_gap"] = max(e["fp_pg"] for e in earlier) - a["fp_pg"] if earlier else NAN
    f["peak_mpg_gap"] = max(e["mpg"] for e in earlier) - a["mpg"] if earlier else NAN
    base = sps_projection(list(prior.values()), season, age, lg)
    f["sps_gain"] = SCORING.fantasy_points(per_game_from_rates(base.rates, base.mpg)) - a["fp_pg"]
    f["sps_mpg_gain"] = base.mpg - a["mpg"]
    if espn is not None and espn.minutes_per_game > 0:
        f["espn_gain"] = SCORING.fantasy_points(espn.per_game) - a["fp_pg"]
        f["espn_mpg_gain"] = espn.minutes_per_game - a["mpg"]
        f["espn_gp_frac"] = espn.gp / season_games(season)
    else:
        f["espn_gain"] = f["espn_mpg_gain"] = f["espn_gp_frac"] = NAN
    return f


def matrix(rows: list[dict[str, float]], features: list[str] = FEATURES) -> np.ndarray:
    return np.array([[r.get(k, NAN) for k in features] for r in rows], dtype=float)


class BreakoutModel:
    """The trained classifier, its probability calibration and its backtest, saved by scripts/train_breakout.py."""

    def __init__(self, bundle: dict):
        self.booster = bundle["booster"]
        self.features: list[str] = bundle["features"]
        self.calibration: tuple[float, float] = tuple(bundle.get("calibration") or (1.0, 0.0))
        self.metrics: dict = bundle.get("metrics", {})

    def _raw(self, rows: list[dict[str, float]]) -> np.ndarray:
        return self.booster.predict(matrix(rows, self.features), raw_score=True)

    def predict(self, rows: list[dict[str, float]]) -> np.ndarray:
        """Breakout probability for each row of features."""
        if not rows:
            return np.zeros(0)
        slope, intercept = self.calibration
        return 1.0 / (1.0 + np.exp(-(slope * self._raw(rows) + intercept)))

    def explain(self, rows: list[dict[str, float]], top: int = 3) -> list[list[dict]]:
        """For each row, what moves the odds most, by kind of evidence: TreeSHAP contributions (log-odds) summed
        within each group of features, largest first."""
        if not rows:
            return []
        out = []
        for contrib in self.booster.predict(matrix(rows, self.features), pred_contrib=True)[:, :-1]:
            groups: dict[str, float] = {}
            for name, value in zip(self.features, contrib):
                g = _GROUP_OF.get(name, name)
                groups[g] = groups.get(g, 0.0) + float(value)
            ranked = sorted(groups.items(), key=lambda kv: -abs(kv[1]))[:top]
            out.append([{"factor": g, "effect": round(v, 3), "direction": "up" if v > 0 else "down"}
                        for g, v in ranked if abs(v) >= 0.02])
        return out

    def reasons(self, row: dict[str, float], top: int = 3) -> list[dict]:
        return self.explain([row], top)[0]

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls, path: str | None = None) -> "BreakoutModel | None":
        p = Path(path) if path else MODEL_PATH
        if not p.exists():
            return None
        try:
            bundle = joblib.load(p)
        except Exception as exc:  # noqa: BLE001 - a broken bundle must not take the app down
            log.warning("could not load the breakout model: %s", exc)
            return None
        return cls(bundle) if bundle.get("features") == FEATURES else None


@lru_cache(maxsize=4)
def _league_rates(directory: str | None = None) -> dict[int, dict[str, float]]:
    return league_rates(load_history(directory).all_lines())


def preseason_rows(ds: HistoryDataset, season: int, lg: dict[int, dict[str, float]],
                   espn: dict[str, ExpertProjection] | None = None,
                   ages: dict[str, float] | None = None) -> dict[str, dict[str, float]]:
    """Features for every player with a level to break out from, going into ``season``. ``espn`` and ``ages``
    fill in what the history file lacks (a league's own data for the season in progress)."""
    out = {}
    for pid, lines in ds.lines.items():
        age = ds.age_in(pid, season)
        if age is None and ages:
            age = ages.get(pid)
        f = build_features(lines, season, age, ds.first_season(pid, season),
                           ds.espn.get((pid, season)) or (espn or {}).get(pid), lg)
        if f is not None:
            out[pid] = f
    return out


def candidates(snapshot: LeagueSnapshot, proj: dict | None = None, model: BreakoutModel | None = None,
               ds: HistoryDataset | None = None) -> list[dict]:
    """Every player with a level to break out from, most likely first, with what this season shows so far.

    ``proj`` (the league's projections) adds each player's rest-of-season projection. A player is "on pace"
    when his season so far already meets the breakout line, over at least 8 games.
    """
    model = model if model is not None else BreakoutModel.load()
    if model is None:
        return []
    ds = ds if ds is not None else load_history()
    season = snapshot.settings.season
    live_espn = {pid: e for pid, p in snapshot.players.items() for e in p.experts if e.season == season}
    ages = {pid: p.age for pid, p in snapshot.players.items() if p.age is not None}
    rows = preseason_rows(ds, season, _league_rates() if ds is load_history() else league_rates(ds.all_lines()),
                          live_espn, ages)
    ids = [pid for pid in rows if pid in snapshot.players]
    probs = model.predict([rows[pid] for pid in ids])
    reasons = model.explain([rows[pid] for pid in ids])
    out = []
    for pid, prob, why in zip(ids, probs, reasons):
        p, f = snapshot.players[pid], rows[pid]
        cur = p.current if p.current and p.current.season == season and p.current.gp else None
        season_fp = fp_per_game(cur) if cur else None
        pr = (proj or {}).get(pid)
        out.append({
            "id": pid, "name": p.name, "pro_team": p.pro_team, "positions": p.positions,
            "injury_status": p.injury_status, "fantasy_team_id": p.fantasy_team_id, "on_waivers": p.on_waivers,
            "age": None if math.isnan(f["age"]) else round(f["age"], 1),
            "years": None if math.isnan(f["years"]) else int(f["years"]),
            "probability": round(float(prob), 4),
            "last_fp_pg": round(f["l1_fp_pg"], 1), "last_mpg": round(f["l1_mpg"], 1),
            "breakout_line": round(max(MIN_LEVEL, f["l1_fp_pg"] + MIN_GAIN), 1),
            "espn_fp_pg": None if math.isnan(f["espn_gain"]) else round(f["l1_fp_pg"] + f["espn_gain"], 1),
            "proj_fp_pg": round(pr.fp_pg, 1) if pr is not None else None,
            "season_fp_pg": round(season_fp, 1) if season_fp is not None else None,
            "season_mpg": round(cur.minutes / cur.gp, 1) if cur else None,
            "games_played": int(cur.gp) if cur else 0,
            "on_pace": bool(cur and cur.gp >= 8 and season_fp >= MIN_LEVEL and season_fp - f["l1_fp_pg"] >= MIN_GAIN),
            "reasons": why,
        })
    out.sort(key=lambda r: -r["probability"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out
