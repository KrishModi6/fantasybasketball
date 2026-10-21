"""Feature construction shared by training and inference.

Every feature is knowable before the target season starts: past box scores,
age, the SPS baseline, and published preseason projections (ESPN).
"""

from __future__ import annotations

from ..baseline import RATE_STATS, Baseline, line_rates, rates_from_per_game, sps_projection
from ..constants import season_games
from ..models import ExpertProjection, SeasonLine

NAN = float("nan")

# Feature groups used to summarise SHAP attributions in plain language.
FEATURE_GROUPS = {
    "sps": "3-season weighted baseline",
    "l1": "Last season's production",
    "l2": "Two seasons ago",
    "espn": "ESPN projection",
    "age": "Age & experience",
    "min": "Minutes history",
    "dur": "Durability",
}


def feature_group(name: str) -> str:
    if name in ("age", "years_pro", "n_seasons"):
        return FEATURE_GROUPS["age"]
    if name.startswith("espn_"):
        return FEATURE_GROUPS["espn"]
    if name in ("sps_avail", "l1_gp_frac", "l2_gp_frac"):
        return FEATURE_GROUPS["dur"]
    if name in ("sps_mpg", "l1_mpg", "l2_mpg", "w_min"):
        return FEATURE_GROUPS["min"]
    return FEATURE_GROUPS.get(name.split("_", 1)[0], name)


def _per36(totals: dict[str, float], minutes: float) -> dict[str, float]:
    if minutes <= 0:
        return {k: NAN for k in RATE_STATS}
    return {k: totals.get(k, 0.0) / minutes * 36 for k in RATE_STATS}


def build_features(history: list[SeasonLine], season: int, age: float | None, years_pro: float | None,
                   espn: ExpertProjection | None, adp: float | None,
                   lg: dict[int, dict[str, float]], baseline: Baseline | None = None) -> tuple[dict[str, float], Baseline]:
    prior = [h for h in history if h.season < season and h.minutes > 0]
    base = baseline or sps_projection(prior, season, age, lg)
    by_season = {h.season: h for h in prior}
    l1 = by_season.get(season - 1)
    l2 = by_season.get(season - 2)

    f: dict[str, float] = {
        "age": age if age is not None else NAN,
        "years_pro": years_pro if years_pro is not None else NAN,
        "n_seasons": float(len([h for h in prior if h.season >= season - 3])),
        "w_min": base.weighted_minutes,
        "sps_mpg": base.mpg,
        "sps_avail": base.availability,
        "l1_mpg": l1.minutes / l1.gp if l1 and l1.gp else NAN,
        "l1_gp_frac": l1.gp / season_games(l1.season) if l1 else 0.0,
        "l2_mpg": l2.minutes / l2.gp if l2 and l2.gp else NAN,
        "l2_gp_frac": l2.gp / season_games(l2.season) if l2 else 0.0,
    }
    for k, v in base.rates.items():
        f[f"sps_{k}"] = v * 36
    l1r = _per36(line_rates(l1), l1.minutes) if l1 else {k: NAN for k in RATE_STATS}
    l2r = _per36(line_rates(l2), l2.minutes) if l2 else {k: NAN for k in RATE_STATS}
    for k in RATE_STATS:
        f[f"l1_{k}"] = l1r[k]
        f[f"l2_{k}"] = l2r[k]
    if espn and espn.minutes_per_game > 0:
        er = rates_from_per_game(espn.per_game, espn.minutes_per_game)
        f["espn_mpg"] = espn.minutes_per_game
        f["espn_gp_frac"] = espn.gp / season_games(season)
        for k in RATE_STATS:
            f[f"espn_{k}"] = er[k] * 36 if k in er else NAN
    else:
        f["espn_mpg"] = NAN
        f["espn_gp_frac"] = NAN
        for k in RATE_STATS:
            f[f"espn_{k}"] = NAN
    return f, base


def feature_names() -> list[str]:
    # ADP is deliberately excluded: it is market sentiment rather than statistics, ESPN wipes it
    # for past seasons (2025-26 is all "undrafted"), and in-season drafts can leak current form.
    names = ["age", "years_pro", "n_seasons", "w_min", "sps_mpg", "sps_avail", "l1_mpg", "l1_gp_frac",
             "l2_mpg", "l2_gp_frac", "espn_mpg", "espn_gp_frac"]
    for k in RATE_STATS:
        names += [f"sps_{k}", f"l1_{k}", f"l2_{k}", f"espn_{k}"]
    return names
