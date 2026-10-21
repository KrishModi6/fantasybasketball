"""Statistical baseline projections (no ML).

Per-minute rates follow Basketball-Reference's Simple Projection System (SPS),
itself modelled on Tom Tango's Marcel:

  * the last three seasons weighted 6 / 3 / 1,
  * regressed toward league average by adding 1,000 minutes of league-average
    production,
  * an aging multiplier of (28 - age) * 0.004 below 28 and (28 - age) * 0.002
    above it (sign reversed for "bad" stats such as misses and turnovers),
  * shooting projected as makes and misses, attempts derived.

SPS deliberately leaves playing time to the user, so minutes per game and
availability use the same weighting + regression idea with their own priors.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .constants import season_games
from .models import SeasonLine

# Stats modelled as per-minute rates. Everything else is derived from these.
RATE_STATS = ["REB", "AST", "STL", "BLK", "TO", "FGM", "FGMI", "FTM", "FTMI", "3PM", "3PMI", "DD", "TD", "PF"]
NEGATIVE_STATS = {"TO", "FGMI", "FTMI", "3PMI", "PF"}

SEASON_WEIGHTS = (6.0, 3.0, 1.0)
REGRESSION_MINUTES = 1000.0
PRIME_AGE = 28.0

# Fallback per-minute league rates (2024-25 ESPN pool) used when a snapshot has no history.
FALLBACK_LEAGUE_RATES = {
    "REB": 0.1826, "AST": 0.1105, "STL": 0.0331, "BLK": 0.0213, "TO": 0.0587,
    "FGM": 0.1715, "FGMI": 0.1990, "FTM": 0.0700, "FTMI": 0.0190, "3PM": 0.0566,
    "3PMI": 0.1035, "DD": 0.0045, "TD": 0.0003, "PF": 0.0802,
}

# Minutes and availability priors.
MPG_PRIOR = 18.0
MPG_PRIOR_GAMES = 6.0
AVAIL_PRIOR = 0.76
AVAIL_PRIOR_GAMES = 160.0  # in weighted games (season weights 6/3/1)


def line_rates(line: SeasonLine) -> dict[str, float]:
    """Totals for the modelled stats (misses computed from attempts)."""
    s = line.stats
    out = {k: s.get(k, 0.0) for k in ("REB", "AST", "STL", "BLK", "TO", "FGM", "FTM", "3PM", "DD", "TD", "PF")}
    out["FGMI"] = max(0.0, s.get("FGA", 0.0) - s.get("FGM", 0.0))
    out["FTMI"] = max(0.0, s.get("FTA", 0.0) - s.get("FTM", 0.0))
    out["3PMI"] = max(0.0, s.get("3PA", 0.0) - s.get("3PM", 0.0))
    if out["REB"] == 0 and (s.get("OREB") or s.get("DREB")):
        out["REB"] = s.get("OREB", 0.0) + s.get("DREB", 0.0)
    return out


def league_rates(lines: list[SeasonLine]) -> dict[int, dict[str, float]]:
    """Per-minute league average for each season, from a pool of player-season lines."""
    tot: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    mins: dict[int, float] = defaultdict(float)
    for ln in lines:
        if ln.minutes <= 0:
            continue
        mins[ln.season] += ln.minutes
        for k, v in line_rates(ln).items():
            tot[ln.season][k] += v
    out = {}
    for season, m in mins.items():
        if m < 50_000:  # too small a pool to be a league average
            continue
        out[season] = {k: tot[season][k] / m for k in RATE_STATS}
    return out


def _lg(season: int, lg: dict[int, dict[str, float]]) -> dict[str, float]:
    if season in lg:
        return lg[season]
    if lg:
        nearest = min(lg, key=lambda s: abs(s - season))
        return lg[nearest]
    return FALLBACK_LEAGUE_RATES


def aging_factor(age: float | None, stat: str) -> float:
    if age is None:
        return 1.0
    adj = (PRIME_AGE - age) * (0.004 if age < PRIME_AGE else 0.002)
    if stat in NEGATIVE_STATS:
        adj = -adj
    return 1.0 + adj


@dataclass
class Baseline:
    rates: dict[str, float]  # per minute
    mpg: float
    availability: float
    weighted_minutes: float  # how much data backs the rates
    seasons_used: list[int]


def sps_projection(history: list[SeasonLine], target_season: int, age: float | None,
                   lg: dict[int, dict[str, float]]) -> Baseline:
    """Project a player's per-minute rates, minutes and availability for ``target_season``."""
    prior = sorted((h for h in history if h.season < target_season and h.minutes > 0),
                   key=lambda h: h.season, reverse=True)
    used = [h for h in prior if h.season >= target_season - 3][:3]

    w_stat: dict[str, float] = defaultdict(float)
    w_lg: dict[str, float] = defaultdict(float)
    w_min = 0.0
    w_gp = 0.0
    w_sg = 0.0
    for h in used:
        w = SEASON_WEIGHTS[target_season - 1 - h.season] if 0 <= target_season - 1 - h.season < 3 else 0.0
        if w == 0:
            continue
        totals = line_rates(h)
        lgr = _lg(h.season, lg)
        for k in RATE_STATS:
            w_stat[k] += w * totals.get(k, 0.0)
            w_lg[k] += w * lgr[k] * h.minutes
        w_min += w * h.minutes
        w_gp += w * h.gp
        w_sg += w * season_games(h.season)

    lg_now = _lg(target_season - 1, lg)
    rates = {}
    for k in RATE_STATS:
        if w_min > 0:
            lg_component = w_lg[k] / w_min * REGRESSION_MINUTES
        else:
            lg_component = lg_now[k] * REGRESSION_MINUTES
        rate = (w_stat[k] + lg_component) / (w_min + REGRESSION_MINUTES)
        rates[k] = rate * aging_factor(age, k)

    # Minutes per game: weighted per-game average shrunk toward a bench-level prior.
    if w_gp > 0:
        mpg = (w_min + MPG_PRIOR * MPG_PRIOR_GAMES) / (w_gp + MPG_PRIOR_GAMES)
    else:
        mpg = MPG_PRIOR
    if age is not None:
        # Young players tend to earn minutes, veterans shed them (same shape as the rate curve, x2).
        mpg *= 1.0 + (PRIME_AGE - age) * (0.008 if age < PRIME_AGE else 0.006)
    mpg = max(4.0, min(mpg, 38.5))

    availability = (w_gp + AVAIL_PRIOR * AVAIL_PRIOR_GAMES) / (w_sg + AVAIL_PRIOR_GAMES)
    availability = max(0.35, min(availability, 0.97))
    return Baseline(rates=rates, mpg=mpg, availability=availability, weighted_minutes=w_min,
                    seasons_used=[h.season for h in used])


def per_game_from_rates(rates: dict[str, float], mpg: float) -> dict[str, float]:
    """Per-game box score from per-minute rates; attempts and points derived from makes + misses."""
    pg = {k: v * mpg for k, v in rates.items()}
    pg["FGA"] = pg.get("FGM", 0) + pg.get("FGMI", 0)
    pg["FTA"] = pg.get("FTM", 0) + pg.get("FTMI", 0)
    pg["3PA"] = pg.get("3PM", 0) + pg.get("3PMI", 0)
    pg["PTS"] = 2 * pg.get("FGM", 0) + pg.get("3PM", 0) + pg.get("FTM", 0)
    pg["MIN"] = mpg
    # DD/TD are per-game events; cap at 1 per game.
    for k in ("DD", "TD"):
        if k in pg:
            pg[k] = min(pg[k], 0.95)
    return pg


def rates_from_per_game(per_game: dict[str, float], mpg: float) -> dict[str, float]:
    """Inverse of per_game_from_rates for expert projections given as per-game lines."""
    if mpg <= 0:
        return {}
    pg = dict(per_game)
    pg.setdefault("FGMI", max(0.0, pg.get("FGA", 0) - pg.get("FGM", 0)))
    pg.setdefault("FTMI", max(0.0, pg.get("FTA", 0) - pg.get("FTM", 0)))
    pg.setdefault("3PMI", max(0.0, pg.get("3PA", 0) - pg.get("3PM", 0)))
    return {k: pg[k] / mpg for k in RATE_STATS if k in pg}
