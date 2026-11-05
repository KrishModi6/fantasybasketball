"""scripts.backtest_advice: the league-by-league comparison of how team 1 was managed (offline)."""

from __future__ import annotations

import pytest

from scripts import backtest_advice as A


def _arm(wins: int, points: float, rank: int, champion: bool = False, moves: int = 20) -> dict:
    return {"record": [wins, 19 - wins, 0], "points_for": points, "rank": rank, "playoffs": rank <= 4,
            "champion": champion, "moves": [[2, "a", "b"]] * moves}


def test_arms_are_compared_league_by_league():
    leagues = [
        {"season": 2025, "league": 0, "arms": {"courtside": _arm(12, 19_000, 2, True), "simple": _arm(10, 18_620, 4),
                                               "averages": _arm(9, 18_430, 6)}},
        {"season": 2026, "league": 0, "arms": {"courtside": _arm(8, 17_100, 7), "simple": _arm(8, 17_005, 6),
                                               "averages": _arm(5, 16_150, 9)}},
    ]
    s = A.summarize(leagues)
    assert s["arms"]["courtside"]["wins"] == 10.0
    assert s["arms"]["courtside"]["playoffs"] == 0.5
    assert s["arms"]["courtside"]["titles"] == 0.5
    d = s["differences"]["courtside - simple"]
    assert d["wins"]["mean"] == 1.0  # +2 and 0
    assert d["points_per_week"]["mean"] == pytest.approx((380 + 95) / 2 / 19)
    assert d["playoffs"]["mean"] == 0.0  # both made it in the first league, neither in the second
    assert d["by_season"] == {2025: 2.0, 2026: 0.0}
    assert s["differences"]["simple - averages"]["wins"]["mean"] == 2.0


def test_a_tie_counts_half_a_win():
    assert A._wins({"record": [9, 9, 1]}) == 9.5
