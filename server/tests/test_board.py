"""Roster moves between refreshes, and the status of shared trade ideas."""

from __future__ import annotations

import os

os.environ.setdefault("FBB_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")

from app.services import board  # noqa: E402
from app.services.board import describe_move, roster_moves, trade_status  # noqa: E402
from fbb.models import RosterEntry  # noqa: E402


def _move(snap, pid: str, to_team: int | None) -> None:
    for t in snap.teams:
        t.roster = [e for e in t.roster if e.player_id != pid]
    if to_team is not None:
        snap.team(to_team).roster.append(RosterEntry(player_id=pid, slot="BE"))


def test_trades_adds_and_drops_are_told_apart(snapshot):
    new = snapshot.model_copy(deep=True)
    t1, t2, t3, t4 = (t.id for t in new.teams[:4])
    a, b = new.team(t1).roster[0].player_id, new.team(t2).roster[0].player_id
    _move(new, a, t2)  # a two-way swap is a trade
    _move(new, b, t1)
    dropped = new.team(t3).roster[0].player_id
    fa = next(p.id for p in snapshot.free_agents())
    _move(new, dropped, None)  # team 3 drops one player and picks up a free agent
    _move(new, fa, t3)
    one_way = new.team(t4).roster[0].player_id
    _move(new, one_way, t1)  # dropped by team 4 and claimed by team 1 between refreshes: not a trade

    moves = roster_moves(snapshot, new)
    trades = [m for m in moves if m["type"] == "trade"]
    assert trades == [{"type": "trade", "teams": [t1, t2], "sent": {str(t1): [a], str(t2): [b]}}]
    waivers = {m["team"]: m for m in moves if m["type"] == "waiver"}
    assert (waivers[t3]["added"], waivers[t3]["dropped"]) == ([fa], [dropped])
    assert (waivers[t1]["added"], waivers[t4]["dropped"]) == ([one_way], [one_way])

    text = [describe_move(m, snapshot, new) for m in moves]
    assert text[0].startswith(f"Trade: {new.team(t1).name} sent {new.players[a].name} to {new.team(t2).name}")
    assert f"{new.team(t3).name} added {new.players[fa].name} and dropped {new.players[dropped].name}" in text


def test_no_moves_on_first_refresh_or_after_a_redraft(snapshot, monkeypatch):
    assert roster_moves(None, snapshot) == []
    assert roster_moves(snapshot, snapshot.model_copy(deep=True)) == []
    monkeypatch.setattr(board, "MAX_MOVES", 5)
    redraft = snapshot.model_copy(deep=True)
    for i, pid in enumerate([p.id for p in snapshot.free_agents()][:6]):
        _move(redraft, pid, redraft.teams[i % len(redraft.teams)].id)
    assert roster_moves(snapshot, redraft) == []  # too many changes at once to be news
    _move(redraft, snapshot.free_agents()[0].id, None)
    assert roster_moves(snapshot, redraft)  # five is still news


def test_trade_status(snapshot):
    t1, t2 = snapshot.teams[0].id, snapshot.teams[1].id
    a, b = snapshot.team(t1).roster[0].player_id, snapshot.team(t2).roster[0].player_id
    sends = {t1: {a: t2}, t2: {b: t1}}
    assert trade_status(snapshot, sends) == ("open", None)

    done = snapshot.model_copy(deep=True)
    _move(done, a, t2)
    _move(done, b, t1)
    assert trade_status(done, sends)[0] == "done"

    gone = snapshot.model_copy(deep=True)
    _move(gone, a, None)
    status, note = trade_status(gone, sends)
    assert status == "stale" and note == f"{snapshot.players[a].name} is now a free agent."
