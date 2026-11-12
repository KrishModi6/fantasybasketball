"""Play a game thousands of times.

The models give a win probability, a predicted margin and a predicted total. A real game lands around those:
in the backtest the final margin missed its prediction by about 14 points (one standard deviation) and the
total by about 19, the two misses unrelated. Each simulated game draws a margin and a total from that
spread and turns them into a final score.

The margin's centre is set so the share of simulated games the home team wins is the model's win probability
(for a Normal margin that centre is spread x the Normal quantile of the probability), so the simulation and the
headline number never disagree. A drawn tie goes to overtime.
"""

from __future__ import annotations

from statistics import NormalDist

import numpy as np

DEFAULT_SIMS = 10_000
MAX_SIMS = 200_000


def consistent_margin(p: float, sigma_margin: float) -> float:
    """The mean margin at which a Normal margin with this spread is positive with probability ``p``."""
    return sigma_margin * NormalDist().inv_cdf(min(max(p, 1e-4), 1 - 1e-4))


def simulate(p: float, total: float, sigma_margin: float, sigma_total: float, rho: float = 0.0, n: int = DEFAULT_SIMS,
             seed: int = 2027) -> dict:
    """``n`` simulated final scores for a game the home team wins with probability ``p`` and that is expected
    to have ``total`` points."""
    n = int(min(max(n, 100), MAX_SIMS))
    rng = np.random.default_rng(seed)
    z1, z2 = rng.standard_normal(n), rng.standard_normal(n)
    margin = consistent_margin(p, sigma_margin) + sigma_margin * z1
    points = total + sigma_total * (rho * z1 + np.sqrt(max(1 - rho * rho, 0.0)) * z2)
    home = np.rint((points + margin) / 2).astype(int)
    away = np.rint((points - margin) / 2).astype(int)
    # A tie after regulation: five more minutes (about 10 points each), and whoever the margin leaned to wins.
    tied = home == away
    if tied.any():
        k = int(tied.sum())
        extra = rng.integers(8, 14, k)
        edge = rng.integers(1, 7, k)
        home_takes = margin[tied] > 0
        home[tied] += extra + np.where(home_takes, edge, 0)
        away[tied] += extra + np.where(home_takes, 0, edge)
    home, away = np.maximum(home, 60), np.maximum(away, 60)
    diff, both = home - away, home + away
    q = lambda x, a: float(np.percentile(x, a))  # noqa: E731
    return {
        "sims": n,
        "home_win": float((diff > 0).mean()),
        "home_score": float(home.mean()), "away_score": float(away.mean()),
        "home_range": [q(home, 10), q(home, 90)], "away_range": [q(away, 10), q(away, 90)],
        "margin": {"mean": float(diff.mean()), "p10": q(diff, 10), "p50": q(diff, 50), "p90": q(diff, 90)},
        "total": {"mean": float(both.mean()), "p10": q(both, 10), "p50": q(both, 50), "p90": q(both, 90)},
        "home_by_10": float((diff >= 10).mean()), "away_by_10": float((diff <= -10).mean()),
        "within_5": float((np.abs(diff) <= 5).mean()), "overtime": float(tied.mean()),
    }
