from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from fbb.trades import find_offers, find_trades

from ..db import get_db
from ..deps import Membership, membership
from ..orm import Activity, TradePost
from ..realtime import hub
from ..schemas import TradeIn, TradePostIn
from ..services.analysis import LeagueAnalysis
from .analysis import analysis

router = APIRouter(prefix="/api/leagues/{league_id}/trades", tags=["trades"])


def _evaluate(a: LeagueAnalysis, body: TradeIn) -> dict:
    teams = {t.id for t in a.snapshot.teams}
    if body.team_a not in teams or body.team_b not in teams or body.team_a == body.team_b:
        raise HTTPException(422, "Pick two different teams in this league")
    try:
        return a.evaluator().evaluate(body.team_a, body.team_b, body.a_gives, body.b_gives)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/evaluate")
async def evaluate(body: TradeIn, a: LeagueAnalysis = Depends(analysis)):
    return await run_in_threadpool(_evaluate, a, body)


@router.get("/suggestions")
async def suggestions(team_id: int, a: LeagueAnalysis = Depends(analysis),
                      max_give: int = Query(2, ge=1, le=3), max_get: int = Query(2, ge=1, le=3),
                      partner_id: int | None = None, exact: bool = False,
                      sizes: str | None = Query(None, pattern=r"^[1-3]-[1-3](,[1-3]-[1-3])*$",
                                                description="Package sizes to search, e.g. 1-1,2-2 (give-get)")):
    if team_id not in {t.id for t in a.snapshot.teams}:
        raise HTTPException(404, "Team not found")
    partners = [partner_id] if partner_id else None
    allowed = frozenset(tuple(int(n) for n in s.split("-")) for s in sizes.split(",")) if sizes else None
    checked: Counter = Counter()
    rows = await run_in_threadpool(find_trades, a.ctx, team_id, a.evaluator(), max_give, max_get, partners,
                                   checked=checked, exact=exact, sizes=allowed)
    for r in rows:
        r["give_names"] = [a.proj[p].name for p in r["give"]]
        r["get_names"] = [a.proj[p].name for p in r["get"]]
    return {"suggestions": rows, "checked": dict(checked)}


@router.get("/offers")
async def offers(team_id: int, a: LeagueAnalysis = Depends(analysis),
                 target: list[str] = Query([], description="Players you want: 1 to 3 from one team (repeat it)"),
                 give: str = Query("1,2,3", pattern=r"^[1-3](,[1-3])*$",
                                   description="How many of your players an offer may include, e.g. 1,2")):
    if team_id not in {t.id for t in a.snapshot.teams}:
        raise HTTPException(404, "Team not found")
    counts = frozenset(int(n) for n in give.split(","))
    checked: Counter = Counter()
    try:
        res = await run_in_threadpool(find_offers, a.ctx, team_id, target, a.evaluator(), counts, checked=checked)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for r in res["offers"] + res["closest"]:
        r["give_names"] = [a.proj[p].name for p in r["give"]]
    res["get_names"] = [a.proj[p].name for p in res["get"]]
    return {**res, "checked": dict(checked)}


@router.get("/board")
def board(m: Membership = Depends(membership), db: Session = Depends(get_db)):
    rows = db.scalars(select(TradePost).where(TradePost.league_id == m.league.id)
                      .order_by(TradePost.created_at.desc()).limit(50)).all()
    return [{"id": r.id, "author": r.author.display_name, "author_id": r.created_by, "team_a": r.team_a,
             "team_b": r.team_b, "a_gives": r.a_gives, "b_gives": r.b_gives, "note": r.note,
             "evaluation": r.evaluation, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/board", status_code=201)
async def post_trade(body: TradePostIn, m: Membership = Depends(membership), a: LeagueAnalysis = Depends(analysis),
                     db: Session = Depends(get_db)):
    ev = await run_in_threadpool(_evaluate, a, body)
    post = TradePost(league_id=m.league.id, created_by=m.user.id, team_a=body.team_a, team_b=body.team_b,
                     a_gives=body.a_gives, b_gives=body.b_gives, note=body.note, evaluation=ev)
    db.add(post)
    names = lambda ids: ", ".join(a.proj[p].name for p in ids) or "nothing"  # noqa: E731
    db.add(Activity(league_id=m.league.id, kind="trade",
                    message=f"{m.user.display_name} shared a trade idea: {names(body.a_gives)} for {names(body.b_gives)}",
                    payload={"team_a": body.team_a, "team_b": body.team_b}))
    db.commit()
    hub.publish(m.league.id, {"type": "trade_shared", "id": post.id, "author": m.user.display_name})
    return {"id": post.id}


@router.delete("/board/{post_id}", status_code=204)
def delete_post(post_id: int, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    post = db.get(TradePost, post_id)
    if post is None or post.league_id != m.league.id:
        raise HTTPException(404, "Not found")
    if post.created_by != m.user.id and not m.is_owner:
        raise HTTPException(403, "Only the author or league owner can remove this")
    db.delete(post)
    db.commit()
    hub.publish(m.league.id, {"type": "trade_removed", "id": post_id})
