"""League votes on trades: everyone in the league votes fair or not fair on trades that went through.

A vote is opinion, like the admin's ruling: it never changes a trade's evaluation, verdict or fairness label, and
nothing is written to ESPN. Only completed trades can be voted on: real trades a refresh found, and board posts
that have since happened (services.rulings). Proposals and ideas can't, so a vote is always about a real deal.

On a shared server every member's vote is a row in trade_votes. Desktop apps each have their own database, so
there the votes go through the Cloudflare Worker, as rulings do (services.rulings_share): each copy holds a random
voter key, the Worker keeps one vote per key, and every copy reads back the trade and the league's tally. A vote
isn't saved until the Worker has it, so the tally a copy shows is always one the whole league agrees on.
"""

from __future__ import annotations

import logging
import math
import secrets
import time
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..orm import League, TradePoll, TradeVote, UserSettings
from ..realtime import hub
from ..security import decrypt_json, encrypt_json
from . import rulings_share as share
from .analysis import LeagueAnalysis
from .rulings import TradeNotFound, _candidates, _score, describe, numbers, review_queue

log = logging.getLogger(__name__)
PULL_EVERY_S = 60.0
_last_pull: dict[str, float] = {}


def _voter_key(db: Session, user_id: str) -> str:
    """This copy's voter key, made the first time it's needed."""
    row = db.get(UserSettings, user_id) or UserSettings(user_id=user_id)
    data = decrypt_json(row.data)
    if not data.get("votes_voter_key"):
        data["votes_voter_key"] = secrets.token_urlsafe(32)
        row.data = encrypt_json(data)
        db.add(row)
        db.commit()
    return data["votes_voter_key"]


def _polls(db: Session, league_id: str) -> dict[str, TradePoll]:
    return {p.trade_key: p for p in db.scalars(select(TradePoll).where(TradePoll.league_id == league_id)).all()}


def _my_vote(db: Session, league_id: str, user_id: str, key: str) -> TradeVote | None:
    return db.scalar(select(TradeVote).where(TradeVote.league_id == league_id, TradeVote.trade_key == key,
                                             TradeVote.user_id == user_id))


def _set_vote(db: Session, league_id: str, user_id: str, key: str, vote: str | None) -> None:
    row = _my_vote(db, league_id, user_id, key)
    if vote is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(TradeVote(league_id=league_id, trade_key=key, user_id=user_id, vote=vote))
    else:
        row.vote = vote


def _take(db: Session, league: League, user_id: str, item: dict, polls: dict[str, TradePoll]) -> bool:
    """Brings one poll into line with what the Worker says. Returns whether it's new to this copy."""
    poll = polls.get(item["key"])
    new = poll is None
    if new:
        poll = TradePoll(league_id=league.id, trade_key=item["key"], trade=item["trade"], numbers=item.get("numbers"),
                         created_at=datetime.fromisoformat(item["at"]))
        db.add(poll)
        polls[item["key"]] = poll
    poll.fair, poll.unfair = int(item["fair"]), int(item["unfair"])
    _set_vote(db, league.id, user_id, item["key"], item.get("mine"))
    return new


def _worker(db: Session, league: League, user_id: str, method: str, key: str = "", body: dict | None = None) -> dict | None:
    """Asks the Worker. None when the poll isn't there; raises share.Unreachable when no answer can be trusted."""
    path = f"/v1/leagues/{share.league_key(league)}/votes" + (f"/{key}" if key else "")
    r = share._call(method, path, body=body, voter=_voter_key(db, user_id))
    if r is not None and r.status_code == 404 and method == "DELETE":
        return None
    if r is None or not r.is_success:
        raise share.Unreachable
    return r.json()


class NotCompleted(ValueError):
    """The trade is on the board but hasn't gone through, so it can't be voted on."""


def completed(row: dict) -> bool:
    """A trade that went through: found on ESPN, or a board post that has since happened."""
    return bool(row["on_espn"]) or row["status"] == "done"


def resolve(db: Session, a: LeagueAnalysis, league_id: str, key: str) -> tuple[dict, dict | None]:
    """What a trade is and Courtside's numbers for it, for a completed trade."""
    item = _candidates(db, league_id).get(key)
    if item is None:
        raise TradeNotFound(key)
    ev, status, _ = _score(a, item)
    if not completed({"on_espn": item["on_espn"], "status": status}):
        raise NotCompleted(key)
    poll = _polls(db, league_id).get(key)
    if poll is not None:
        return poll.trade, poll.numbers
    return describe(a.snapshot, item["sends"], ev), numbers(ev)


def cast(db: Session, league: League, user_id: str, key: str, vote: str | None, trade: dict,
         numbers_: dict | None) -> bool:
    """Opens the poll if it isn't open, and votes if `vote` is given. Returns whether the poll is new here."""
    polls = _polls(db, league.id)
    if share.reach(league) == "league":
        item = _worker(db, league, user_id, "PUT", key, {"vote": vote, "trade": trade, "numbers": numbers_})
        return _take(db, league, user_id, item, polls)
    new = key not in polls
    if new:
        db.add(TradePoll(league_id=league.id, trade_key=key, trade=trade, numbers=numbers_))
    if vote:
        _set_vote(db, league.id, user_id, key, vote)
    return new


def withdraw(db: Session, league: League, user_id: str, key: str) -> None:
    if share.reach(league) == "league":
        item = _worker(db, league, user_id, "DELETE", key)
        if item is not None:
            _take(db, league, user_id, item, _polls(db, league.id))
            return
    _set_vote(db, league.id, user_id, key, None)


def pull(db: Session, league: League, user_id: str) -> bool:
    """Reads the league's tallies into this copy. Returns whether a poll arrived that it didn't have."""
    if share.reach(league) != "league":
        return False
    data = _worker(db, league, user_id, "GET")
    polls = _polls(db, league.id)
    arrived = False
    for item in data["polls"]:
        if _take(db, league, user_id, item, polls):
            arrived = True
    db.commit()
    if arrived:
        hub.publish(league.id, {"type": "trade_voted"})
    return arrived


def pull_if_due(db: Session, league: League, user_id: str) -> None:
    """For page loads: at most once a minute per league, and never a reason for the page to fail."""
    now = time.monotonic()
    if now - _last_pull.get(league.id, -math.inf) < PULL_EVERY_S:
        return
    _last_pull[league.id] = now
    try:
        pull(db, league, user_id)
    except Exception:  # noqa: BLE001 - votes arriving late must never break the page that asked
        log.info("votes: pull failed for %s", league.id, exc_info=True)
        db.rollback()


def _tallies(db: Session, league: League, polls: dict[str, TradePoll]) -> dict[str, tuple[int, int]]:
    if share.reach(league) == "league":
        return {k: (p.fair, p.unfair) for k, p in polls.items()}
    counts = {k: [0, 0] for k in polls}
    rows = db.execute(select(TradeVote.trade_key, TradeVote.vote, func.count()).where(TradeVote.league_id == league.id)
                      .group_by(TradeVote.trade_key, TradeVote.vote)).all()
    for key, vote, n in rows:
        if key in counts:
            counts[key][0 if vote == "fair" else 1] = n
    return {k: (f, u) for k, (f, u) in counts.items()}


def poll_out(key: str, tallies: dict[str, tuple[int, int]], mine: dict[str, str]) -> dict:
    fair, unfair = tallies.get(key, (0, 0))
    return {"open": key in tallies, "fair": fair, "unfair": unfair, "mine": mine.get(key)}


def vote_list(db: Session, a: LeagueAnalysis, league: League, user_id: str) -> list[dict]:
    """Every completed trade, newest first, with Courtside's numbers, the league's tally and this member's vote."""
    polls = _polls(db, league.id)
    tallies = _tallies(db, league, polls)
    mine = {v.trade_key: v.vote for v in db.scalars(select(TradeVote).where(
        TradeVote.league_id == league.id, TradeVote.user_id == user_id)).all()}
    rows = [r for r in review_queue(db, a, league.id) if completed(r)]
    for r in rows:
        r["votes"] = poll_out(r["key"], tallies, mine)
        if r["numbers"] is None and r["key"] in polls:
            r["numbers"] = polls[r["key"]].numbers
    return sorted(rows, key=lambda r: r["at"], reverse=True)
