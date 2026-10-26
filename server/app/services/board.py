"""Keeps the league's trade board and feed in step with the league itself.

After every refresh: real roster moves (trades, adds, drops) found by comparing the new rosters
with the previous ones go into the activity feed, and recent board posts are re-scored with the new
data, or marked as done or no longer possible when the rosters say so.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from fbb.models import LeagueSnapshot

from ..orm import League, TradePost
from .analysis import get_analysis

log = logging.getLogger(__name__)
BOARD_REFRESH_LIMIT = 20  # most recent posts re-scored after each refresh (about 0.1 s each)
MAX_MOVES = 40  # more players changing teams than this between two refreshes is a draft or a new season
MOVE_SOURCES = ("espn", "flaim")  # demo leagues re-draft by ADP, so their rosters drift without real moves


def _owners(snap: LeagueSnapshot) -> dict[str, int]:
    return {e.player_id: t.id for t in snap.teams for e in t.roster}


def roster_moves(prev: LeagueSnapshot | None, new: LeagueSnapshot) -> list[dict]:
    """Trades, adds and drops between two refreshes, worked out from where each rostered player sits.

    A player who leaves one team for another counts toward a trade only if both teams sent someone;
    a one-way move is a drop and a waiver pickup that happened between the two refreshes.
    """
    if prev is None:
        return []
    before, after = _owners(prev), _owners(new)
    changed = sorted(pid for pid in before.keys() | after.keys() if before.get(pid) != after.get(pid))
    if len(changed) > MAX_MOVES:
        return []
    sent: dict[tuple[int, int], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    adds: dict[int, list[str]] = defaultdict(list)
    drops: dict[int, list[str]] = defaultdict(list)
    for pid in changed:
        a, b = before.get(pid), after.get(pid)
        if a is not None and b is not None:
            sent[(a, b)][a].append(pid)
            continue
        if a is None:
            adds[b].append(pid)
        else:
            drops[a].append(pid)
    moves = []
    pairs = {tuple(sorted(k)) for k in sent}
    for x, y in sorted(pairs):
        x_sent, y_sent = sent[(x, y)][x], sent[(y, x)][y]
        if x_sent and y_sent:
            moves.append({"type": "trade", "teams": [x, y], "sent": {str(x): x_sent, str(y): y_sent}})
        else:  # one-way: dropped by one team, picked up by the other
            giver, taker, pids = (x, y, x_sent) if x_sent else (y, x, y_sent)
            drops[giver].extend(pids)
            adds[taker].extend(pids)
    for team in sorted(adds.keys() | drops.keys()):
        moves.append({"type": "waiver", "team": team, "added": adds.get(team, []), "dropped": drops.get(team, [])})
    return moves


def describe_move(move: dict, prev: LeagueSnapshot, new: LeagueSnapshot) -> str:
    def name(pid: str) -> str:
        p = new.players.get(pid) or prev.players.get(pid)
        return p.name if p else pid

    def names(pids: list[str]) -> str:
        return ", ".join(name(p) for p in pids)

    def team(tid: int) -> str:
        return new.team(tid).name

    if move["type"] == "trade":
        x, y = move["teams"]
        return f"Trade: {team(x)} sent {names(move['sent'][str(x)])} to {team(y)} for {names(move['sent'][str(y)])}"
    added, dropped = move["added"], move["dropped"]
    if added and dropped:
        return f"{team(move['team'])} added {names(added)} and dropped {names(dropped)}"
    return f"{team(move['team'])} {'added' if added else 'dropped'} {names(added or dropped)}"


def post_sends(post: TradePost) -> dict[int, dict[str, int]]:
    """Who sends whom where in a board post (see TradeEvaluator.evaluate_multi).

    The table has columns for a two-team trade; a three-team post keeps its moves with the evaluation.
    """
    ev = post.evaluation or {}
    if len(ev.get("teams", [])) > 2:
        sends: dict[int, dict[str, int]] = {t: {} for t in ev["teams"]}
        for m in ev["moves"]:
            sends[m["from_team"]][m["player_id"]] = m["to_team"]
        return sends
    return {post.team_a: {p: post.team_b for p in post.a_gives}, post.team_b: {p: post.team_a for p in post.b_gives}}


def trade_status(snap: LeagueSnapshot, sends: dict[int, dict[str, int]]) -> tuple[str, str | None]:
    """open: still possible. done: the rosters show it happened. stale: a player has moved elsewhere."""
    owner = _owners(snap)
    moves = [(pid, home, to) for home, out in sends.items() for pid, to in out.items()]
    if moves and all(owner.get(pid) == to for pid, _, to in moves):
        return "done", "This trade has gone through."
    for pid, home, _ in moves:
        now = owner.get(pid)
        if now != home:
            who = snap.players[pid].name if pid in snap.players else pid
            return "stale", f"{who} is now {'on ' + snap.team(now).name if now is not None else 'a free agent'}."
    return "open", None


def refresh_board(db: Session, league: League) -> int:
    """Re-score the most recent board posts with the league's latest data. Returns how many changed."""
    posts = db.scalars(select(TradePost).where(TradePost.league_id == league.id)
                       .order_by(TradePost.created_at.desc()).limit(BOARD_REFRESH_LIMIT)).all()
    if not posts:
        return 0
    analysis = get_analysis(db, league)
    if analysis is None:
        return 0
    evaluator = analysis.evaluator()
    now = datetime.now(timezone.utc).isoformat()
    for post in posts:
        sends = post_sends(post)
        status, note = trade_status(analysis.snapshot, sends)
        evaluation = dict(post.evaluation)
        if status == "open":
            try:
                evaluation = evaluator.evaluate_multi(sends)
                evaluation["as_of"] = now
            except ValueError as exc:  # e.g. a roster can no longer fit the incoming players
                status, note = "stale", str(exc)
        evaluation["status"], evaluation["status_note"] = status, note
        post.evaluation = evaluation  # a new dict, so the JSON column is saved
    db.commit()
    return len(posts)
