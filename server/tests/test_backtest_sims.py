"""scripts.backtest_sims: switching settings, and the summary of a season still being played (offline)."""

from __future__ import annotations

import pytest

import fbb.context as C
from scripts import backtest_sims as B


def test_app_config_is_the_app_as_shipped():
    shipped = C.MISS_NEXT_GAME
    try:
        B.apply_config(B.CONFIGS["base"])
        assert C.MISS_NEXT_GAME == 1.0  # the flat miss rate the study started from
    finally:
        B.apply_config(B.CONFIGS["app"])
    assert C.MISS_NEXT_GAME == shipped
    assert "app" not in B.STUDY and B.GROUPS[B.APP_GROUP] == ["app"]


def _row(mp: int, a: int, p: float, real_a: float, real_b: float) -> dict:
    row = {"cfg": "app", "mp": mp, "a": a, "b": a + 1, "p_mc": p, "p_norm": p, "real_a": real_a, "real_b": real_b,
           "m_p10": -40.0, "m_p90": 40.0}
    for side, real in (("a", real_a), ("b", real_b)):
        row.update({f"mean_{side}": 500.0, f"sd_{side}": 40.0, f"q_{side}": [450, 475, 500, 525, 550],
                    f"pit_{side}": 0.5, f"norm_mean_{side}": 500.0, f"norm_sd_{side}": 40.0,
                    f"starts_{side}": 38.0, f"real_starts_{side}": 38.0})
        row[f"real_{side}"] = real
    return row


def test_summary_of_a_season_in_progress_goes_week_by_week():
    # Week 1: both favourites win and every score lands in its 80% range. Week 2: the favourite loses one,
    # and one score (600) is above its range.
    weekly = [_row(1, 1, 0.7, 520, 480), _row(1, 3, 0.4, 490, 510),
              _row(2, 1, 0.8, 470, 530), _row(2, 3, 0.6, 600, 500)]
    s = B.summarize([{"season": 2027, "league": 0, "weekly": weekly, "odds": [], "outcome": None}])
    by_week = s["weekly"]["app"]["by_week"]
    assert by_week[1] == {"matchups": 2, "brier": pytest.approx((0.3 ** 2 + 0.4 ** 2) / 2), "favourites_won": 1.0,
                          "range80": 1.0}
    assert by_week[2]["favourites_won"] == 0.5
    assert by_week[2]["range80"] == 0.75
    assert s["season"] == {}  # no playoff odds to check until the season is over
