"""scripts.backtest_advice: how its managers judge players, and the league-by-league comparison (offline)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import backtest_advice as A


class _Season:
    """What the managers' numbers read: one team playing every day, weeks starting on days 1, 8, ... 50."""

    def __init__(self):
        self.mps = {mp: [7 * mp - 6] for mp in range(1, 9)}
        self.schedule = SimpleNamespace(games={d: ["BOS"] for d in range(1, 60)})
        self.actual = {
            "star": {d: 50.0 for d in range(1, 6)},  # hurt since day 6
            "scrub": {d: 15.0 for d in range(1, 49)},
            "hot": {d: 10.0 for d in range(1, 23)} | {d: 40.0 for d in range(23, 29)},
        }
        pr = {p: SimpleNamespace(pro_team="BOS", fp_pg=pg, games_out=0.0)
              for p, pg in (("star", 48.0), ("scrub", 16.0), ("hot", 12.0), ("rookie", 20.0))}
        self.proj = {mp: pr for mp in self.mps}


def test_a_hurt_star_stays_until_his_manager_gives_up_on_him():
    s = _Season()
    assert A._absent(s, 2, "star") == 2  # days 6 and 7
    assert A._keep_value(s, 2, "star", "averages") == 50.0
    assert A._keep_value(s, 2, "star", "averages") > A._keep_value(s, 2, "scrub", "averages")
    assert A._absent(s, 4, "star") == 16
    assert A._keep_value(s, 4, "star", "averages") == 50.0
    assert A._absent(s, 5, "star") == A.GIVE_UP + 3
    assert A._keep_value(s, 5, "star", "averages") == 0.0


def test_each_arm_ranks_players_by_its_own_numbers():
    s = _Season()
    assert A._keep_value(s, 5, "hot", "averages") == pytest.approx((22 * 10 + 6 * 40) / 28)
    assert A._keep_value(s, 5, "hot", "recent") == pytest.approx((9 * 10 + 6 * 40) / 15)  # days 14-28
    assert A._keep_value(s, 5, "hot", "projections") == 12.0
    assert A._keep_value(s, 5, "hot", "courtside") == 12.0
    assert A._per_game(s, 5, "rookie") == 20.0  # hasn't played: his preseason projection


def test_a_pickup_counts_the_games_he_can_play_in_two_weeks():
    s = _Season()
    assert A._pickup_value(s, 2, "scrub") == pytest.approx(15.0 * 14)
    s = _Season()
    s.proj[2]["scrub"].games_out = 4.0
    assert A._pickup_value(s, 2, "scrub") == pytest.approx(15.0 * 10)


def _arm(wins: int, points: float, rank: int, champion: bool = False, moves: int = 20) -> dict:
    return {"record": [wins, 19 - wins, 0], "points_for": points, "rank": rank, "playoffs": rank <= 4,
            "champion": champion, "moves": [[2, "a", "b"]] * moves}


def test_arms_are_compared_league_by_league():
    leagues = [
        {"season": 2025, "league": 0, "arms": {"courtside": _arm(12, 19_000, 2, True), "averages": _arm(10, 18_620, 4),
                                               "projections": _arm(9, 18_430, 6)}},
        {"season": 2026, "league": 0, "arms": {"courtside": _arm(8, 17_100, 7), "averages": _arm(8, 17_005, 6),
                                               "projections": _arm(5, 16_150, 9)}},
    ]
    s = A.summarize(leagues)
    assert s["arms"]["courtside"]["wins"] == 10.0
    assert s["arms"]["courtside"]["playoffs"] == 0.5
    assert s["arms"]["courtside"]["titles"] == 0.5
    assert s["by_season"]["courtside"][2025]["wins"] == 12.0
    d = s["differences"]["courtside - averages"]
    assert d["wins"]["mean"] == 1.0  # +2 and 0
    assert d["points_per_week"]["mean"] == pytest.approx((380 + 95) / 2 / 19)
    assert d["playoffs"]["mean"] == 0.0  # both made it in the first league, neither in the second
    assert d["by_season"][2025]["wins"] == 2.0
    assert d["by_season"][2026]["wins"] == 0.0
    assert s["differences"]["projections - averages"]["wins"]["mean"] == -2.0
    assert "recent - averages" not in s["differences"]  # that arm didn't run


def test_a_tie_counts_half_a_win():
    assert A._wins({"record": [9, 9, 1]}) == 9.5
