"""The league admin's trade rulings. Only the admin can see the dashboard or rule; every member can read the rulings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Membership, admin_membership, membership, owner_membership
from ..orm import Activity, TradeRuling
from ..realtime import hub
from ..schemas import AdminCodeIn, RulingIn
from ..services import rulings_share as share
from ..services.analysis import LeagueAnalysis
from ..services.rulings import TradeNotFound, league_rulings, review_queue, ruling_message, ruling_out, save_ruling
from .analysis import analysis

router = APIRouter(prefix="/api/leagues/{league_id}/trades", tags=["rulings"])


ELSEWHERE = "Someone else is already this league's admin, so this copy can't rule on its trades"


def _refused(db: Session, m: Membership) -> HTTPException:
    """Forget what was about to change and remember what the Worker said, so this copy stops offering to rule."""
    db.rollback()
    share.note_admin_elsewhere(m.league, True)
    db.commit()
    return HTTPException(403, ELSEWHERE)


@router.post("/admin-code")
def admin_code(body: AdminCodeIn, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    """For the league's admin when their copy can't claim the league itself: it came from an invite code, or a new computer."""
    if share.reach(m.league) != "league":
        raise HTTPException(400, "Admin codes only work for ESPN leagues in the desktop app.")
    try:
        share.redeem_code(db, m.league, body.code.strip())
    except share.Refused:
        raise HTTPException(403, "That code isn't right for this league.") from None
    except share.Unreachable:
        raise HTTPException(503, "Couldn't check that code just now. Try again in a minute.") from None
    return {"is_admin": True}


@router.get("/review")
def review(m: Membership = Depends(admin_membership), a: LeagueAnalysis = Depends(analysis),
           db: Session = Depends(get_db)):
    share.pull_if_due(db, m.league)  # also resends anything that couldn't be sent earlier
    return {"reach": share.reach(m.league), "trades": review_queue(db, a, m.league.id)}


@router.get("/rulings")
def rulings(m: Membership = Depends(membership), db: Session = Depends(get_db)):
    share.pull_if_due(db, m.league)
    return [ruling_out(r) for r in league_rulings(db, m.league.id)]


@router.put("/rulings/{key}")
def rule(key: str, body: RulingIn, m: Membership = Depends(admin_membership), a: LeagueAnalysis = Depends(analysis),
         db: Session = Depends(get_db)):
    previous = db.scalar(select(TradeRuling.ruling).where(TradeRuling.league_id == m.league.id, TradeRuling.trade_key == key))
    if share.reach(m.league) == "league":
        share.ensure_admin_key(db, m.user.id)
    try:
        row, changed = save_ruling(db, a, m.league.id, m.user.id, key, body.ruling, body.note)
        share.publish(db, m.league, m.user.id, row)
    except TradeNotFound:
        raise HTTPException(404, "That trade isn't on the board or in the league's recent moves") from None
    except share.Refused:
        raise _refused(db, m) from None
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
    try:
        share.unpublish(db, m.league, m.user.id, row)
    except share.Refused:
        raise _refused(db, m) from None
    except share.Unreachable:
        raise HTTPException(503, "Couldn't reach the rest of the league, so this ruling still stands. Try again.") from None
    db.add(Activity(league_id=m.league.id, kind="ruling",
                    message=f"League admin withdrew a ruling: {row.trade.get('text', '')}"[:500], payload={"key": key}))
    db.delete(row)
    db.commit()
    hub.publish(m.league.id, {"type": "trade_ruled", "key": key})
