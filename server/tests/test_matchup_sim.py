"""Monte Carlo matchup simulator tests (offline, synthetic league)."""

from __future__ import annotations

import numpy as np
import pytest

from factories import make_snapshot
from fbb import context
from fbb.context import AnalysisContext
from fbb.matchup_sim import EVENTS, MatchupSimulator, event_rates, events_to_stats, fp_weights, week_options
from fbb.models import ScoringSettings
from fbb.projections import ProjectionEngine


def _ctx(categories: bool = False) -> AnalysisContext:
    snap = make_snapshot()
    if categories:
        snap.settings.scoring = ScoringSettings(kind="categories",
                                                categories=["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"])
    return AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())


def test_event_decomposition_reproduces_box_score():
    pg = {"FGA": 15, "FGM": 7, "3PA": 5, "3PM": 2, "FTA": 4, "FTM": 3, "REB": 6, "AST": 4, "STL": 1, "BLK": 0.5, "TO": 2}
    stats = events_to_stats(event_rates(pg))
    for k in ("FGA", "FGM", "3PA", "3PM", "FTA", "FTM", "REB", "AST", "TO"):
        assert stats[k] == pytest.approx(pg[k])
    assert stats["PTS"] == pytest.approx(2 * 7 + 2 + 3)  # 2*FGM + 3PM + FTM
    w = fp_weights(ScoringSettings())
    assert float(event_rates(pg) @ w) == pytest.approx(ScoringSettings().fantasy_points({**pg, "PTS": 19}))
    assert len(w) == len(EVENTS)


def test_player_spread_is_calibrated():
    ctx = _ctx()
    sim = MatchupSimulator(ctx, seed=1)
    pid = ctx.team_roster(1)[0]
    pl = sim._player(pid)
    s = sim.rng.gamma(pl.shape, 1 / pl.shape, 100_000)
    fp = sim.rng.poisson(pl.lam[None, :] * s[:, None]) @ sim.w_fp
    pr = ctx.proj[pid]
    assert fp.mean() == pytest.approx(pr.fp_pg, rel=0.02)
    assert fp.std() == pytest.approx(pr.fp_sd, rel=0.05)


def test_points_matchup_probabilities_and_consistency():
    ctx = _ctx()
    mp = 2
    r = MatchupSimulator(ctx, seed=3).simulate(1, 2, mp, n=6000)
    a, b = r["teams"]
    assert a["win_prob"] + b["win_prob"] + r["tie_prob"] == pytest.approx(1.0)
    assert a["win_prob"] > 0.5  # team 1 is built stronger
    analytic = ctx.project_window(ctx.team_roster(1), ctx.matchup_days(mp)).mean
    assert a["mean"] == pytest.approx(analytic, rel=0.05)
    assert sum(a["hist"]) == 6000 and len(r["bins"]) == len(a["hist"]) + 1
    assert a["p10"] <= a["median"] <= a["p90"]


def test_week_level_swings_widen_both_weekly_spreads(monkeypatch):
    """The simulator and the analytic weekly window carry the same week-level swings (fbb.context)."""
    ctx = _ctx()
    mp = 2
    roster, days = ctx.team_roster(1), ctx.matchup_days(mp)

    def spreads():
        sim = MatchupSimulator(ctx, seed=5)
        players, plans = sim._plan(roster, days, set())
        totals = sim._simulate_team(players, plans, 20_000)[0] @ sim.w_fp
        return totals.std(), np.sqrt(ctx.project_window(roster, days).by_mp[mp][1])

    sim_sd, analytic_sd = spreads()
    assert sim_sd == pytest.approx(analytic_sd, rel=0.15)
    monkeypatch.setattr(context, "PLAYER_WEEK_CV", 0.0)
    monkeypatch.setattr(context, "TEAM_WEEK_CV", 0.0)
    narrow_sim, narrow_analytic = spreads()
    assert narrow_sim < 0.95 * sim_sd and narrow_analytic < 0.95 * analytic_sd


def test_sitting_a_star_hurts():
    ctx = _ctx()
    sim = MatchupSimulator(ctx, seed=5)
    base = sim.simulate(1, 2, 2, n=5000)
    star = base["teams"][0]["players"][0]["player_id"]
    sat = MatchupSimulator(ctx, seed=5).simulate(1, 2, 2, exclude={star}, n=5000)
    assert sat["teams"][0]["mean"] < base["teams"][0]["mean"]
    assert sat["teams"][0]["win_prob"] < base["teams"][0]["win_prob"]
    row = next(p for p in sat["teams"][0]["players"] if p["player_id"] == star)
    assert row["excluded"] and row["games"] == 0


def test_category_matchup():
    ctx = _ctx(categories=True)
    r = MatchupSimulator(ctx, seed=7).simulate(1, 3, 3, n=4000)
    cats = r["categories"]
    assert len(cats["rows"]) == 9
    assert sum(cats["a_cats_won_dist"]) == pytest.approx(1.0)
    for row in cats["rows"]:
        assert row["a_win"] + row["b_win"] + row["tie"] == pytest.approx(1.0)
    fg = next(x for x in cats["rows"] if x["category"] == "FG%")
    assert 0.3 < fg["a_mean"] < 0.7


def test_rejects_bad_requests():
    ctx = _ctx()
    sim = MatchupSimulator(ctx)
    with pytest.raises(ValueError):
        sim.simulate(1, 1, 2)
    with pytest.raises(ValueError):
        sim.simulate(1, 2, 999)
    weeks = week_options(ctx)
    assert weeks[0]["is_current"] and all(np.diff([w["matchup_period"] for w in weeks]) > 0)
