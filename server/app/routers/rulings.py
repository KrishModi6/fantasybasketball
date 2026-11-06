"""The league admin's trade rulings. Only the admin can see the dashboard or rule; every member can read the rulings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Membership, admin_membership, membership
from ..orm import Activity, TradeRuling
from ..realtime import hub
from ..schemas import RulingIn
from ..services.analysis import LeagueAnalysis
from ..services.rulings import TradeNotFound, league_rulings, review_queue, ruling_message, ruling_out, save_ruling
from .analysis import analysis

router = APIRouter(prefix="/api/leagues/{league_id}/trades", tags=["rulings"])


@router.get("/review")
def review(m: Membership = Depends(admin_membership), a: LeagueAnalysis = Depends(analysis),
           db: Session = Depends(get_db)):
    return review_queue(db, a, m.league.id)


@router.get("/rulings")
def rulings(m: Membership = Depends(membership), db: Session = Depends(get_db)):
    return [ruling_out(r) for r in league_rulings(db, m.league.id)]


@router.put("/rulings/{key}")
def rule(key: str, body: RulingIn, m: Membership = Depends(admin_membership), a: LeagueAnalysis = Depends(analysis),
         db: Session = Depends(get_db)):
    previous = db.scalar(select(TradeRuling.ruling).where(TradeRuling.league_id == m.league.id, TradeRuling.trade_key == key))
    try:
        row, changed = save_ruling(db, a, m.league.id, m.user.id, key, body.ruling, body.note)
    except TradeNotFound:
        raise HTTPException(404, "That trade isn't on the board or in the league's recent moves") from None
    if changed:
        db.add(Activity(league_id=m.league.id, kind="ruling", message=ruling_message(row, previous),
                        payload={"key": key, "ruling": body.ruling}))
    db.commit()
    hub.publish(m.league.id, {"type": "trade_ruled", "key": key})
    return ruling_out(row)


@router.delete("/rulings/{key}", status_code=204)
def withdraw(key: str, m: Membership = Depends(admin_membership), db: Session = Depends(get_db)):
    row = db.scalar(select(TradeRuling).where(TradeRuling.league_id == m.league.id, TradeRuling.trade_key == key))
    if row is None:
        raise HTTPException(404, "There's no ruling on that trade")
    db.add(Activity(league_id=m.league.id, kind="ruling",
                    message=f"League admin withdrew a ruling: {row.trade.get('text', '')}"[:500], payload={"key": key}))
    db.delete(row)
    db.commit()
    hub.publish(m.league.id, {"type": "trade_ruled", "key": key})
