"""What the API presents, built from a real league analysis of a factory league."""

from __future__ import annotations

import os

os.environ.setdefault("FBB_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")

from app.services.analysis import LeagueAnalysis  # noqa: E402
from app.services.present import weekly_points  # noqa: E402
from factories import make_snapshot  # noqa: E402


def _in_week_2(scored: bool):
    snap = make_snapshot()
    snap.current_period = snap.settings.matchup_periods[2][0]
    snap.current_matchup_period = 2
    if scored:
        for m in snap.matchups:
            if m.matchup_period == 1:
                m.home_score, m.away_score, m.winner = 1250.0, 1180.5, "HOME"
    return snap


def test_weekly_table_starts_with_the_final_scores_of_played_weeks():
    snap = _in_week_2(scored=True)
    wk = weekly_points(LeagueAnalysis("t", 1, snap))
    assert [w["matchup_period"] for w in wk["weeks"]][:2] == [1, 2]
    assert wk["weeks"][0]["final"] and not wk["weeks"][0]["current"]
    assert not wk["weeks"][1]["final"] and wk["weeks"][1]["current"]

    m = next(m for m in snap.matchups if m.matchup_period == 1)
    week1 = {t["team_id"]: t["weeks"][0] for t in wk["teams"]}
    assert week1[m.home_team_id] == {"matchup_period": 1, "proj": None, "sd": None, "scored": None, "win_prob": None,
                                     "opponent_id": m.away_team_id, "final": 1250.0, "opp_final": 1180.5, "result": "W"}
    assert week1[m.away_team_id]["result"] == "L" and week1[m.away_team_id]["final"] == 1180.5
    assert all(t["weeks"][1]["proj"] > 0 for t in wk["teams"])  # the weeks left are still projected


def test_weekly_table_leaves_out_played_weeks_without_results():
    wk = weekly_points(LeagueAnalysis("t", 1, _in_week_2(scored=False)))  # like a demo league
    assert wk["weeks"][0]["matchup_period"] == 2 and not any(w["final"] for w in wk["weeks"])
