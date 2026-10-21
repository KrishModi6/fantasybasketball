"""Player value measures.

Points leagues
  ROS points = FP/G x expected remaining games (schedule x availability x injury)
  VORP       = (FP/G - replacement FP/G) x expected remaining games, where the
               replacement level is what a manager can actually pick up: the
               median FP/G of the five best free agents.

Category leagues
  G-scores (Rosenof 2023): like Z-scores, but the denominator includes the
  week-to-week noise of each category, so volatile categories (steals,
  blocks) are not over-rewarded:  G_c = (x_c - mu_c) / sqrt(sigma_c^2 + tau_c^2 / G)
  with sigma_c the spread of per-game means across rosterable players, tau_c^2
  the per-game variance (Poisson approximation) and G ~ 3.5 games per week.
  Percentage categories use volume-weighted impact: makes - attempts x league %.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from .constants import LOWER_IS_BETTER, RATIO_STATS

if TYPE_CHECKING:
    from .context import AnalysisContext

GAMES_PER_WEEK = 3.5


def _cat_value(per_game: dict[str, float], cat: str, league_pct: dict[str, float]) -> float:
    if cat in RATIO_STATS:
        num, den = RATIO_STATS[cat]
        made = per_game.get(num, 0.0) if num != "EFGM" else per_game.get("FGM", 0.0) + 0.5 * per_game.get("3PM", 0.0)
        return made - per_game.get(den, 0.0) * league_pct.get(cat, 0.0)
    return per_game.get(cat, 0.0)


class CategoryValuer:
    def __init__(self, ctx: "AnalysisContext"):
        self.ctx = ctx
        self.cats = ctx.scoring.categories
        projs = list(ctx.proj.values())
        n_pool = max(50, ctx.s.settings.team_count * 13)
        pool = sorted(projs, key=lambda p: -(p.fp_pg * p.availability))[:n_pool]
        self.league_pct: dict[str, float] = {}
        for cat in self.cats:
            if cat in RATIO_STATS:
                num, den = RATIO_STATS[cat]
                m = sum((p.per_game.get("FGM", 0) + 0.5 * p.per_game.get("3PM", 0)) if num == "EFGM"
                        else p.per_game.get(num, 0) for p in pool)
                a = sum(p.per_game.get(den, 0) for p in pool)
                self.league_pct[cat] = m / a if a else 0.0
        self.mu: dict[str, float] = {}
        self.denom: dict[str, float] = {}
        for cat in self.cats:
            vals = np.array([_cat_value(p.per_game, cat, self.league_pct) for p in pool])
            mu, sigma2 = float(vals.mean()), float(vals.var())
            if cat in RATIO_STATS:
                _, den = RATIO_STATS[cat]
                pct = self.league_pct.get(cat, 0.0)
                tau2 = float(np.mean([p.per_game.get(den, 0) * pct * (1 - pct) for p in pool]))
            else:
                tau2 = float(np.mean(np.abs(vals))) * 1.3  # overdispersed Poisson
            self.mu[cat] = mu
            self.denom[cat] = math.sqrt(sigma2 + tau2 / GAMES_PER_WEEK) or 1.0
        self._cache: dict[str, dict[str, float]] = {}

    def gscores(self, pid: str) -> dict[str, float]:
        if pid in self._cache:
            return self._cache[pid]
        pg = self.ctx.proj[pid].per_game
        out = {}
        for cat in self.cats:
            g = (_cat_value(pg, cat, self.league_pct) - self.mu[cat]) / self.denom[cat]
            out[cat] = -g if cat in LOWER_IS_BETTER else g
        self._cache[pid] = out
        return out

    def value_pg(self, pid: str) -> float:
        # Shift so a replacement-level player is ~0 and values stay positive for lineup weights.
        return max(0.01, sum(self.gscores(pid).values()) + len(self.cats) * 0.5)


def replacement_fp(ctx: "AnalysisContext") -> float:
    fas = [ctx.value_pg[p.id] for p in ctx.s.free_agents() if p.id in ctx.proj and ctx.proj[p.id].games_out < 3]
    fas.sort(reverse=True)
    top = fas[:5]
    return float(np.median(top)) if top else 0.0


def player_values(ctx: "AnalysisContext") -> dict[str, dict]:
    repl = replacement_fp(ctx)
    out = {}
    for pid, pr in ctx.proj.items():
        games = ctx.expected_games(pid)
        v = ctx.value_pg[pid]
        out[pid] = {
            "value_pg": v,
            "ros_games": games,
            "ros_value": v * games,
            "vorp": (v - repl) * games,
        }
    return out
