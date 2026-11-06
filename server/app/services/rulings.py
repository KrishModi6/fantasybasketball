"""The league admin's rulings on trades.

A ruling is a person's call, kept apart from Courtside's numbers: making one never changes a trade's
evaluation, verdict or fairness label. It's also only a label. Courtside reads from ESPN and never writes to
it, so a ruling can't block or undo anything there.

What the admin can rule on: trades on the league board (which includes any trade the admin analyzes and
posts) and real trades found when a refresh compares rosters. Each desktop copy has its own board, so real
trades are the only ones that reach the admin's copy without being entered.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from fbb.models import LeagueSnapshot

from ..orm import Activity, TradePost, TradeRuling
from .analysis import LeagueAnalysis
from .board import post_sends, trade_status

Sends = dict[int, dict[str, int]]
BOARD_LIMIT = 50
REAL_TRADE_LIMIT = 20  # newest real trades listed; each one that isn't on the board gets scored
ACTIVITY_SCAN = 500  # how far back to look for them among the league's other roster moves
SIDE_NUMBERS = ("team_id", "team_name", "delta_per_week", "delta_pct", "before_playoff_odds", "after_playoff_odds",
                "before_title_odds", "after_title_odds", "verdict")
WORDS = {"fair": "fair", "unfair": "not fair"}


class TradeNotFound(LookupError):
    """No trade the admin can rule on, and no earlier ruling, has this key."""


def trade_key(sends: Sends) -> str:
    """Names a trade by who goes where, so a board post and the same deal on ESPN share one ruling."""
    moves = sorted(f"{p}:{t}>{to}" for t, out in sends.items() for p, to in out.items())
    return hashlib.sha256("|".join(moves).encode()).hexdigest()[:32]


def _iso(dt: datetime) -> str:
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).isoformat()


def numbers(ev: dict | None) -> dict | None:
    """What the dashboard shows from a full evaluation, and what a ruling keeps a copy of."""
    if not ev:
        return None
    fairness = ev.get("fairness", {})
    return {"sides": [{k: s.get(k) for k in SIDE_NUMBERS} for s in ev.get("sides", [])],
            "fairness": {"label": fairness.get("label", ""), "reason": fairness.get("reason", "")},
            "summary": ev.get("summary", ""), "completed": bool(ev.get("completed"))}


def describe(snap: LeagueSnapshot, sends: Sends, ev: dict | None = None) -> dict:
    """Who sends whom, in names that still read after the rosters change."""
    known = {r["player_id"]: r["name"] for r in (ev or {}).get("players", [])}

    def name(pid: str) -> str:
        return snap.players[pid].name if pid in snap.players else known.get(pid, pid)

    teams = {t.id: t.name for t in snap.teams}
    team = lambda t: teams.get(t, f"Team {t}")  # noqa: E731
    joined = lambda pids: ", ".join(name(p) for p in pids) or "nothing"  # noqa: E731
    ids = list(sends)
    if len(ids) == 2:
        x, y = ids
        text = f"{team(x)} sends {joined(sends[x])} to {team(y)} for {joined(sends[y])}"
    else:
        text = "; ".join(f"{team(t)} sends {name(p)} to {team(to)}" for t, out in sends.items() for p, to in out.items())
    return {"teams": [{"id": t, "name": team(t)} for t in ids],
            "moves": [{"player_id": p, "name": name(p), "from_team": t, "to_team": to}
                      for t, out in sends.items() for p, to in out.items()],
            "text": text}


def activity_sends(payload: dict) -> Sends:
    """The same shape as post_sends, from a real trade the refresh logged (services.board.roster_moves)."""
    x, y = payload["teams"]
    return {x: {p: y for p in payload["sent"][str(x)]}, y: {p: x for p in payload["sent"][str(y)]}}


def _candidates(db: Session, league_id: str) -> dict[str, dict]:
    """Every trade the admin can rule on, by key, not scored yet. A deal on both the board and ESPN appears once."""
    found: dict[str, dict] = {}
    posts = db.scalars(select(TradePost).where(TradePost.league_id == league_id)
                       .order_by(TradePost.created_at.desc()).limit(BOARD_LIMIT)).all()
    for post in posts:
        sends = post_sends(post)
        found.setdefault(trade_key(sends), {"sends": sends, "post": post, "at": post.created_at, "on_espn": False})
    rows = db.scalars(select(Activity).where(Activity.league_id == league_id, Activity.kind == "transaction")
                      .order_by(Activity.created_at.desc()).limit(ACTIVITY_SCAN)).all()
    real = [r for r in rows if (r.payload or {}).get("type") == "trade"][:REAL_TRADE_LIMIT]
    for row in real:
        sends = activity_sends(row.payload)
        key = trade_key(sends)
        if key in found:
            found[key]["on_espn"] = True
        else:
            found[key] = {"sends": sends, "post": None, "at": row.created_at, "on_espn": True}
    return found


def _score(a: LeagueAnalysis, item: dict) -> tuple[dict | None, str, str | None]:
    """A trade's evaluation and status. A board post is kept scored by every refresh; a real trade is scored here."""
    post: TradePost | None = item["post"]
    if post is not None:
        ev = post.evaluation or {}
        return ev, ev.get("status", "open"), ev.get("status_note")
    status, note = trade_status(a.snapshot, item["sends"])
    if status != "done":  # someone has moved on since, so there's no roster left to undo it on
        owner = {e.player_id: t.id for t in a.snapshot.teams for e in t.roster}
        moved = next((p for out in item["sends"].values() for p, to in out.items() if owner.get(p) != to), None)
        who = a.snapshot.players[moved].name if moved in a.snapshot.players else moved
        return None, "stale", f"{who} has moved since this trade, so its numbers can't be worked out any more." if moved else note
    try:
        return a.evaluator().evaluate_completed(item["sends"]), "done", None
    except ValueError as exc:
        return None, "stale", str(exc)


def ruling_out(r: TradeRuling) -> dict:
    return {"key": r.trade_key, "ruling": r.ruling, "note": r.note, "trade": r.trade, "numbers": r.numbers,
            "decided_at": _iso(r.decided_at)}


def league_rulings(db: Session, league_id: str) -> list[TradeRuling]:
    return list(db.scalars(select(TradeRuling).where(TradeRuling.league_id == league_id)
                           .order_by(TradeRuling.decided_at.desc())).all())


def review_queue(db: Session, a: LeagueAnalysis, league_id: str) -> list[dict]:
    """The admin dashboard: each trade with Courtside's own numbers and any ruling, newest first."""
    rulings = {r.trade_key: r for r in league_rulings(db, league_id)}
    candidates = _candidates(db, league_id)
    rows = []
    for key, item in sorted(candidates.items(), key=lambda kv: _iso(kv[1]["at"]), reverse=True):
        ev, status, status_note = _score(a, item)
        post: TradePost | None = item["post"]
        rows.append({"key": key, "at": _iso(item["at"]), "on_espn": item["on_espn"],
                     "post": {"id": post.id, "author": post.author.display_name, "note": post.note} if post else None,
                     "status": status, "status_note": status_note, "trade": describe(a.snapshot, item["sends"], ev),
                     "numbers": numbers(ev), "ruling": ruling_out(rulings[key]) if key in rulings else None})
    # A ruling outlives the post or feed entry it was made from, and can still be changed or withdrawn.
    for key, r in rulings.items():
        if key not in candidates:
            rows.append({"key": key, "at": _iso(r.decided_at), "on_espn": False, "post": None, "status": "gone",
                         "status_note": None, "trade": r.trade, "numbers": None, "ruling": ruling_out(r)})
    return rows


def save_ruling(db: Session, a: LeagueAnalysis, league_id: str, user_id: str, key: str, ruling: str,
                note: str) -> tuple[TradeRuling, bool]:
    """Makes or changes a ruling, with a copy of Courtside's numbers as they stand. Returns it and whether the call changed."""
    item = _candidates(db, league_id).get(key)
    row = db.scalar(select(TradeRuling).where(TradeRuling.league_id == league_id, TradeRuling.trade_key == key))
    if item is None and row is None:
        raise TradeNotFound(key)
    ev = _score(a, item)[0] if item else None
    changed = row is None or row.ruling != ruling
    if row is None:
        row = TradeRuling(league_id=league_id, trade_key=key, ruling=ruling, decided_by=user_id,
                          trade=describe(a.snapshot, item["sends"], ev))
        db.add(row)
    row.ruling, row.note, row.decided_by = ruling, note, user_id
    if changed:
        row.decided_at = datetime.now(timezone.utc)
    if ev:  # with nothing to score any more, keep the copy from the last time there was
        row.numbers = numbers(ev)
    return row, changed


def ruling_message(row: TradeRuling, changed_from: str | None = None) -> str:
    action = f"changed a ruling to {WORDS[row.ruling]}" if changed_from else f"ruled a trade {WORDS[row.ruling]}"
    return f"League admin {action}: {row.trade.get('text', '')}"[:500]
