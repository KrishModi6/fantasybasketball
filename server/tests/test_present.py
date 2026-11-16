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


def test_player_rows_say_which_teammate_is_out():
    """The Players list and the player card get the teammate effect: the % and the star by name."""
    from app.services.present import player_detail, player_rows
    from fbb.context import AnalysisContext

    a = LeagueAnalysis("t", 1, make_snapshot())
    by_team: dict[str, list[str]] = {}
    for pid in a.proj:
        by_team.setdefault(a.snapshot.players[pid].pro_team, []).append(pid)
    pids = max(by_team.values(), key=len)
    star = max(pids, key=lambda pid: a.proj[pid].fp_pg)
    a.proj[star].games_out = 5
    a.ctx = AnalysisContext(a.snapshot, a.proj)  # the analysis already worked out availability without it
    rows = {r["id"]: r for r in player_rows(a)}
    lifted = [rows[pid] for pid in pids if pid != star and rows[pid]["teammates"]]
    assert lifted, "someone on the star's team should be lifted"
    t = lifted[0]["teammates"]
    assert t["pct"] > 0 and t["out"][0]["id"] == star and t["out"][0]["name"] == a.snapshot.players[star].name
    assert rows[star]["teammates"] is None or rows[star]["teammates"]["out"][0]["id"] != star
    assert player_detail(a, lifted[0]["id"])["teammates"] == t
    other = [r for r in rows.values() if a.snapshot.players[r["id"]].pro_team != a.snapshot.players[star].pro_team]
    assert all(r["teammates"] is None for r in other)  # nobody else is out
