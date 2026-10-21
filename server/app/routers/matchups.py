"""Weekly matchups and the Monte Carlo matchup simulator."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from fbb.matchup_sim import DEFAULT_SIMS, MatchupSimulator, week_options
from fbb.strength import MatchupModel

from ..schemas import SimulateIn
from ..services.analysis import LeagueAnalysis
from .analysis import analysis

router = APIRouter(prefix="/api/leagues/{league_id}/matchups", tags=["matchups"])


@router.get("")
def matchups(a: LeagueAnalysis = Depends(analysis), matchup_period: int | None = Query(None, ge=1)):
    """A week's scheduled matchups with quick (Normal-approximation) projections."""
    ctx = a.ctx
    weeks = week_options(ctx)
    if not weeks:
        return {"weeks": [], "matchup_period": None, "matchups": []}
    valid = {w["matchup_period"] for w in weeks}
    mp = matchup_period if matchup_period in valid else weeks[0]["matchup_period"]
    mm = MatchupModel(ctx, a.windows)
    rows = []
    for m in ctx.s.matchups:
        if m.matchup_period != mp or m.away_team_id is None:
            continue
        hm, hv = mm.dist(m.home_team_id, mp)
        am, av = mm.dist(m.away_team_id, mp)
        rows.append({
            "home_id": m.home_team_id, "away_id": m.away_team_id,
            "home_name": ctx.s.team(m.home_team_id).name, "away_name": ctx.s.team(m.away_team_id).name,
            "home_proj": hm, "away_proj": am, "home_sd": hv ** 0.5, "away_sd": av ** 0.5,
            "home_score": m.home_score, "away_score": m.away_score,
            "home_win_prob": mm.p_win(m.home_team_id, m.away_team_id, mp),
        })
    return {"weeks": weeks, "matchup_period": mp, "matchups": rows,
            "scoring": "points" if ctx.is_points else "categories"}


@router.post("/simulate")
async def simulate(body: SimulateIn, a: LeagueAnalysis = Depends(analysis)):
    teams = {t.id for t in a.snapshot.teams}
    if body.team_a not in teams or body.team_b not in teams:
        raise HTTPException(404, "Team not found")

    def run():
        # Fixed seed per request: the same question gives the same answer, so toggling a player is a clean comparison.
        return MatchupSimulator(a.ctx, seed=body.seed).simulate(
            body.team_a, body.team_b, body.matchup_period, set(body.exclude), body.sims)

    try:
        return await run_in_threadpool(run)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


__all__ = ["router", "DEFAULT_SIMS"]
