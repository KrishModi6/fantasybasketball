"""Read-only analytics: rankings, teams, players, lineups, news, activity, overrides."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from fbb.improve import lineup_plan, team_report

from ..db import get_db
from ..deps import Membership, membership
from ..orm import Activity, PlayerOverrideRow, ProjectionHistory
from ..realtime import hub
from ..schemas import OverrideIn
from ..services.analysis import LeagueAnalysis, get_analysis, invalidate
from ..services.present import player_detail, player_rows, strength_rows, team_detail, weekly_points

router = APIRouter(prefix="/api/leagues/{league_id}", tags=["analysis"])


def analysis(m: Membership = Depends(membership), db: Session = Depends(get_db)) -> LeagueAnalysis:
    a = get_analysis(db, m.league)
    if a is None:
        detail = "This league has no data yet."
        if m.league.last_sync_status == "error":
            detail += f" Last sync failed: {m.league.last_sync_error}"
        raise HTTPException(409, detail)
    return a


def _team(a: LeagueAnalysis, team_id: int) -> None:
    if team_id not in {t.id for t in a.snapshot.teams}:
        raise HTTPException(404, "Team not found")


@router.get("/rankings")
async def rankings(a: LeagueAnalysis = Depends(analysis)):
    rows = await run_in_threadpool(strength_rows, a)
    return {"teams": rows, "as_of": _as_of(a), "scoring": a.snapshot.settings.scoring.kind}


@router.get("/weekly")
async def weekly(a: LeagueAnalysis = Depends(analysis)):
    """Projected points for every team in every remaining week."""
    return {**await run_in_threadpool(weekly_points, a), "as_of": _as_of(a)}


@router.get("/teams/{team_id}")
async def team(team_id: int, a: LeagueAnalysis = Depends(analysis)):
    _team(a, team_id)
    return await run_in_threadpool(team_detail, a, team_id)


@router.get("/teams/{team_id}/report")
async def report(team_id: int, a: LeagueAnalysis = Depends(analysis)):
    _team(a, team_id)
    return await run_in_threadpool(team_report, a.ctx, team_id, a.windows)


@router.get("/teams/{team_id}/lineup")
async def lineup(team_id: int, days: int = Query(7, ge=1, le=14), a: LeagueAnalysis = Depends(analysis)):
    _team(a, team_id)
    return {"days": await run_in_threadpool(lineup_plan, a.ctx, team_id, days)}


@router.get("/players")
def players(a: LeagueAnalysis = Depends(analysis),
            status: str = Query("all", pattern="^(all|fa|rostered)$"),
            pos: str | None = Query(None, max_length=3), q: str | None = Query(None, max_length=60),
            team_id: int | None = None, sort: str = Query("ros_value"), limit: int = Query(200, ge=1, le=700)):
    ids = []
    ql = q.lower().strip() if q else None
    for pid, p in a.snapshot.players.items():
        if status == "fa" and p.fantasy_team_id is not None:
            continue
        if status == "rostered" and p.fantasy_team_id is None:
            continue
        if team_id is not None and p.fantasy_team_id != team_id:
            continue
        if pos and pos.upper() not in p.positions:
            continue
        if ql and ql not in p.name.lower():
            continue
        ids.append(pid)
    rows = player_rows(a, ids)
    key = sort.lstrip("-")
    allowed = {"ros_value", "fp_pg", "vorp", "games_next7", "mpg", "season_fp_pg", "recent_fp_pg", "rank",
               "ownership_pct", "value_pg", "xfp_pg", "luck_pg"}
    if key not in allowed:
        key = "ros_value"
    reverse = key != "rank"
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0) if reverse else (r.get(key) or 0)))
    return {"players": rows[:limit], "total": len(rows), "as_of": _as_of(a)}


@router.get("/players/{player_id}")
def player(player_id: str, m: Membership = Depends(membership), a: LeagueAnalysis = Depends(analysis),
           db: Session = Depends(get_db)):
    if player_id not in a.proj:
        raise HTTPException(404, "Player not found")
    hist = db.scalars(select(ProjectionHistory).where(ProjectionHistory.league_id == m.league.id,
                                                      ProjectionHistory.player_id == player_id)
                      .order_by(ProjectionHistory.as_of)).all()
    out = player_detail(a, player_id, hist)
    ov = db.scalar(select(PlayerOverrideRow).where(PlayerOverrideRow.league_id == m.league.id,
                                                   PlayerOverrideRow.player_id == player_id))
    out["override"] = ({"minutes_pct": ov.minutes_pct, "out_games": ov.out_games, "note": ov.note} if ov else None)
    return out


@router.put("/players/{player_id}/override")
def set_override(player_id: str, body: OverrideIn, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    """League-wide, bounded manual adjustment (e.g. a confirmed role change). Logged to the activity feed."""
    row = db.scalar(select(PlayerOverrideRow).where(PlayerOverrideRow.league_id == m.league.id,
                                                    PlayerOverrideRow.player_id == player_id))
    if row is None:
        row = PlayerOverrideRow(league_id=m.league.id, player_id=player_id, created_by=m.user.id)
        db.add(row)
    row.minutes_pct = body.minutes_pct
    row.out_games = body.out_games
    row.note = body.note
    db.add(Activity(league_id=m.league.id, kind="override",
                    message=f"{m.user.display_name} adjusted a projection: {body.note or 'no note'}",
                    payload={"player_id": player_id, "minutes_pct": body.minutes_pct, "out_games": body.out_games}))
    db.commit()
    invalidate(m.league.id)
    hub.publish(m.league.id, {"type": "override_changed", "player_id": player_id})
    return {"ok": True}


@router.delete("/players/{player_id}/override", status_code=204)
def clear_override(player_id: str, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    row = db.scalar(select(PlayerOverrideRow).where(PlayerOverrideRow.league_id == m.league.id,
                                                    PlayerOverrideRow.player_id == player_id))
    if row:
        db.delete(row)
        db.commit()
        invalidate(m.league.id)
        hub.publish(m.league.id, {"type": "override_changed", "player_id": player_id})


@router.get("/news")
def news(a: LeagueAnalysis = Depends(analysis)):
    rostered = {e.player_id: t.id for t in a.snapshot.teams for e in t.roster}
    out = []
    for n in a.snapshot.news[:40]:
        d = n.model_dump(mode="json")
        d["players"] = [{"id": pid, "name": a.snapshot.players[pid].name, "fantasy_team_id": rostered.get(pid)}
                        for pid in n.player_ids if pid in a.snapshot.players]
        out.append(d)
    return out


@router.get("/activity")
def activity(m: Membership = Depends(membership), db: Session = Depends(get_db), limit: int = Query(40, le=200)):
    rows = db.scalars(select(Activity).where(Activity.league_id == m.league.id)
                      .order_by(Activity.created_at.desc()).limit(limit)).all()
    return [{"id": r.id, "kind": r.kind, "message": r.message, "payload": r.payload,
             "created_at": r.created_at.isoformat()} for r in rows]


def _as_of(a: LeagueAnalysis) -> dict:
    d = a.snapshot.schedule.period_dates.get(a.snapshot.current_period)
    return {"date": d.isoformat() if d else None, "fetched_at": a.snapshot.fetched_at.isoformat()
            if a.snapshot.fetched_at else None, "matchup_period": a.snapshot.current_matchup_period}
