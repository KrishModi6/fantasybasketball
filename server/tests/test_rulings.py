"""The league admin's trade rulings: who can rule, what a ruling records, and what it leaves alone."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.orm import Activity, League, LeagueMember, Snapshot, User
from app.security import create_token, encrypt_json
from app.services.analysis import invalidate, pack_snapshot
from app.services.board import describe_move, roster_moves
from app.services.rulings import describe, numbers, trade_key
from factories import make_snapshot
from fbb.context import AnalysisContext
from fbb.models import RosterEntry
from fbb.projections import ProjectionEngine
from fbb.trades import TradeEvaluator


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _user(name: str = "Someone") -> tuple[str, dict]:
    with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4().hex}@example.com", display_name=name, password_hash="!")
        db.add(user)
        db.commit()
        return user.id, {"Authorization": f"Bearer {create_token(user.id)}"}


def _league(owner_id: str, snap=None, cfg: dict | None = None) -> str:
    with SessionLocal() as db:
        league = League(name="Test", season=2027, source="espn", owner_id=owner_id, last_sync_status="ok",
                        connector_config=encrypt_json(cfg or {"league_id": 1}))
        db.add(league)
        db.flush()
        db.add(LeagueMember(league_id=league.id, user_id=owner_id, role="owner"))
        db.add(Snapshot(league_id=league.id, source="espn", data=pack_snapshot(snap or make_snapshot())))
        db.commit()
        return league.id


def _join(league_id: str, user_id: str) -> None:
    with SessionLocal() as db:
        db.add(LeagueMember(league_id=league_id, user_id=user_id, role="member"))
        db.commit()


def _evaluator(snap) -> TradeEvaluator:
    return TradeEvaluator(AnalysisContext(snap, ProjectionEngine(snap, None).project_all()))


def _lopsided(snap) -> tuple[int, int, str, str]:
    """Two teams, the first team's best player and the second team's worst: nobody could call this even."""
    proj = ProjectionEngine(snap, None).project_all()
    x, y = snap.teams[0].id, snap.teams[1].id
    best = max((e.player_id for e in snap.team(x).roster), key=lambda p: proj[p].fp_pg)
    worst = min((e.player_id for e in snap.team(y).roster), key=lambda p: proj[p].fp_pg)
    return x, y, best, worst


def _trade_body(x: int, y: int, a: str, b: str, note: str = "") -> dict:
    return {"team_a": x, "team_b": y, "a_gives": [a], "b_gives": [b], "note": note}


def _move(snap, pid: str, to_team: int | None) -> None:
    for team in snap.teams:
        team.roster = [e for e in team.roster if e.player_id != pid]
    if to_team is not None:
        snap.team(to_team).roster.append(RosterEntry(player_id=pid, slot="BE"))
    snap.players[pid].fantasy_team_id = to_team


def _refresh(league_id: str, prev, new) -> None:
    """What a refresh does with rosters: a new snapshot, plus the moves it found in the feed."""
    with SessionLocal() as db:
        db.add(Snapshot(league_id=league_id, source="espn", data=pack_snapshot(new)))
        for move in roster_moves(prev, new):
            db.add(Activity(league_id=league_id, kind="transaction", message=describe_move(move, prev, new), payload=move))
        db.commit()
    invalidate(league_id)


def _traded(snap, x: int, y: int, a: str, b: str):
    new = snap.model_copy(deep=True)
    _move(new, a, y)
    _move(new, b, x)
    return new


# ---- who can rule -----------------------------------------------------------------------------------


def test_only_the_admin_gets_the_dashboard(client):
    owner_id, owner = _user("Owner")
    member_id, member = _user("Member")
    _, stranger = _user("Stranger")
    lid = _league(owner_id)
    _join(lid, member_id)

    assert client.get(f"/api/leagues/{lid}", headers=owner).json()["me"]["is_admin"] is True
    assert client.get(f"/api/leagues/{lid}", headers=member).json()["me"]["is_admin"] is False

    key = "0" * 32
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=owner).status_code == 200
    for who, code in ((member, 403), (stranger, 404), ({}, 401)):
        assert client.get(f"/api/leagues/{lid}/trades/review", headers=who).status_code == code
        assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=who).status_code == code
        assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=who).status_code == code


def test_a_member_cant_rule_on_a_real_trade(client):
    owner_id, owner = _user()
    member_id, member = _user()
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    _join(lid, member_id)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(x, y, a, b), headers=member).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]["key"]

    assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=member).status_code == 403
    assert client.get(f"/api/leagues/{lid}/trades/rulings", headers=member).json() == []


def test_owning_a_copy_from_an_invite_code_doesnt_make_you_admin(client):
    # On the desktop app everyone is the owner of their own copy, so the owner role can't be the test.
    friend_id, friend = _user("Friend")
    lid = _league(friend_id, cfg={"league_id": 1, "espn_s2": "the-admins", "swid": "{ADMIN}", "shared_by": "the league owner"})
    me = client.get(f"/api/leagues/{lid}", headers=friend).json()["me"]
    assert me["role"] == "owner" and me["is_admin"] is False
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=friend).status_code == 403
    assert client.put(f"/api/leagues/{lid}/trades/rulings/{'0' * 32}", json={"ruling": "fair"}, headers=friend).status_code == 403

    # Pasting their own cookies drops shared_by, but it's still a copy.
    body = {"espn": {"league_id": "1","espn_s2": "my-own", "swid": "{ME}"}}
    assert client.patch(f"/api/leagues/{lid}", json=body, headers=friend).status_code == 200
    assert client.get(f"/api/leagues/{lid}", headers=friend).json()["me"]["is_admin"] is False
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=friend).status_code == 403


def test_replacing_cookies_keeps_a_connected_league_its_admin(client):
    owner_id, owner = _user()
    lid = _league(owner_id)
    body = {"espn": {"league_id": "1","espn_s2": "fresh", "swid": "{ME}"}}
    assert client.patch(f"/api/leagues/{lid}", json=body, headers=owner).status_code == 200
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=owner).status_code == 200


# ---- what a ruling records -----------------------------------------------------------------------------


def test_ruling_on_a_board_trade(client):
    owner_id, owner = _user("Owner")
    member_id, member = _user("Member")
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    _join(lid, member_id)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(x, y, a, b, "thoughts?"), headers=member).status_code == 201

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()
    assert row["post"]["author"] == "Member" and row["post"]["note"] == "thoughts?"
    assert row["status"] == "open" and row["ruling"] is None and row["on_espn"] is False
    assert row["trade"]["text"] == (f"{snap.team(x).name} sends {snap.players[a].name} to {snap.team(y).name} "
                                    f"for {snap.players[b].name}")
    # The app's own numbers, for both teams, from the analyzer.
    assert [s["team_id"] for s in row["numbers"]["sides"]] == [x, y]
    assert row["numbers"]["completed"] is False and row["numbers"]["fairness"]["label"]
    for side in row["numbers"]["sides"]:
        assert side["delta_per_week"] is not None and side["verdict"]
        assert 0 <= side["before_playoff_odds"] <= 1 and 0 <= side["after_title_odds"] <= 1

    evaluation = client.get(f"/api/leagues/{lid}/trades/board", headers=owner).json()[0]["evaluation"]
    r = client.put(f"/api/leagues/{lid}/trades/rulings/{row['key']}", json={"ruling": "unfair", "note": "too lopsided"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["ruling"] == "unfair" and r.json()["note"] == "too lopsided"
    assert r.json()["numbers"] == row["numbers"]  # a copy of what Courtside said when the call was made
    assert r.json()["trade"] == row["trade"]

    # Everyone in the league sees it: as a ruling, on the board and in the feed.
    assert [x["ruling"] for x in client.get(f"/api/leagues/{lid}/trades/rulings", headers=member).json()] == ["unfair"]
    post = client.get(f"/api/leagues/{lid}/trades/board", headers=member).json()[0]
    assert post["ruling"] == "unfair"
    feed = client.get(f"/api/leagues/{lid}/activity", headers=member).json()
    assert feed[0]["kind"] == "ruling" and feed[0]["message"].startswith("League admin ruled a trade not fair:")

    # The ruling changes nothing about the app's own result.
    assert post["evaluation"] == evaluation


def test_changing_and_withdrawing_a_ruling(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]["key"]
    url = f"/api/leagues/{lid}/trades/rulings/{key}"
    feed = lambda: [a["message"] for a in client.get(f"/api/leagues/{lid}/activity", headers=owner).json() if a["kind"] == "ruling"]  # noqa: E731

    assert client.put(url, json={"ruling": "fair"}, headers=owner).status_code == 200
    assert client.put(url, json={"ruling": "fair", "note": "on reflection"}, headers=owner).json()["note"] == "on reflection"
    assert len(feed()) == 1  # a new note on the same call isn't news
    assert client.put(url, json={"ruling": "unfair"}, headers=owner).json()["ruling"] == "unfair"
    assert feed()[0].startswith("League admin changed a ruling to not fair:")
    assert len(client.get(f"/api/leagues/{lid}/trades/rulings", headers=owner).json()) == 1

    assert client.delete(url, headers=owner).status_code == 204
    assert client.get(f"/api/leagues/{lid}/trades/rulings", headers=owner).json() == []
    assert client.get(f"/api/leagues/{lid}/trades/board", headers=owner).json()[0]["ruling"] is None
    assert feed()[0].startswith("League admin withdrew a ruling:")
    assert client.delete(url, headers=owner).status_code == 404


def test_bad_rulings_are_refused(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]["key"]
    url = f"/api/leagues/{lid}/trades/rulings"
    assert client.put(f"{url}/{key}", json={"ruling": "maybe"}, headers=owner).status_code == 422
    assert client.put(f"{url}/{key}", json={"ruling": "fair", "note": "x" * 501}, headers=owner).status_code == 422
    assert client.put(f"{url}/{'0' * 32}", json={"ruling": "fair"}, headers=owner).status_code == 404


def test_a_key_from_another_league_finds_nothing(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    other = _league(owner_id, snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]["key"]
    assert client.put(f"/api/leagues/{other}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).status_code == 404


def test_a_ruling_outlives_the_post_it_was_made_on(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    r = client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner)
    row = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]
    url = f"/api/leagues/{lid}/trades/rulings/{row['key']}"
    assert client.put(url, json={"ruling": "unfair"}, headers=owner).status_code == 200
    assert client.delete(f"/api/leagues/{lid}/trades/board/{r.json()['id']}", headers=owner).status_code == 204

    [gone] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()
    assert gone["status"] == "gone" and gone["numbers"] is None and gone["trade"] == row["trade"]
    changed = client.put(url, json={"ruling": "fair"}, headers=owner).json()
    assert changed["ruling"] == "fair" and changed["numbers"] == row["numbers"]  # nothing new to copy, so the old copy stays
    assert client.delete(url, headers=owner).status_code == 204
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json() == []


# ---- real trades ---------------------------------------------------------------------------------------


def test_a_finished_trade_reads_like_the_trade_itself(snapshot):
    x, y, a, b = _lopsided(snapshot)
    before = _evaluator(snapshot).evaluate(x, y, [a], [b])
    done = _evaluator(_traded(snapshot, x, y, a, b)).evaluate_completed({x: {a: y}, y: {b: x}})

    assert done["completed"] is True and "completed" not in before
    assert done["moves"] == before["moves"]  # the trade as it was made, not the undo
    for was, now in zip(before["sides"], done["sides"]):
        assert (now["team_id"], now["gives"], now["receives"]) == (was["team_id"], was["gives"], was["receives"])
        assert now["delta_per_week"] == pytest.approx(was["delta_per_week"], abs=max(2.0, abs(was["delta_per_week"]) * 0.5))
        assert now["delta_per_week"] * was["delta_per_week"] > 0
        assert now["verdict"] == was["verdict"]
        assert now["delta_ros"] == pytest.approx(now["after_ros"] - now["before_ros"])
        # What the undo said about going back is now the before and after of the trade.
        assert (now["after_playoff_odds"] > now["before_playoff_odds"]) == (now["delta_per_week"] > 0) or \
            now["after_playoff_odds"] == now["before_playoff_odds"]
        assert now["auto_drops"] == [] and now["auto_adds"] == []
    assert done["fairness"]["label"] == before["fairness"]["label"]
    assert snapshot.team(x).name in done["summary"]


def test_a_real_trade_shows_up_with_its_numbers_labelled(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    new = _traded(snap, x, y, a, b)
    third = new.teams[2]  # a waiver move in the same refresh isn't a trade to rule on
    fa = new.free_agents()[0].id
    _move(new, fa, third.id)
    _refresh(lid, snap, new)

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()
    assert row["on_espn"] is True and row["post"] is None and row["status"] == "done"
    assert row["numbers"]["completed"] is True  # the page says it was worked out by undoing the trade
    assert row["numbers"]["sides"][0]["delta_per_week"] < 0 < row["numbers"]["sides"][1]["delta_per_week"]
    assert row["key"] == trade_key({x: {a: y}, y: {b: x}})

    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{row['key']}", json={"ruling": "unfair"}, headers=owner).json()
    assert saved["numbers"] == row["numbers"] and saved["trade"]["moves"][0]["name"] == new.players[a].name


def test_a_trade_on_the_board_and_on_espn_is_one_row_with_one_ruling(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(x, y, a, b), headers=owner).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()[0]["key"]
    assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).status_code == 200

    _refresh(lid, snap, _traded(snap, x, y, a, b))
    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()
    assert row["key"] == key and row["on_espn"] is True and row["post"] is not None
    assert row["ruling"]["ruling"] == "fair"
    assert row["numbers"]["completed"] is False  # the analyzer's own score from before the deal


def test_a_trade_whose_players_have_moved_on_can_still_be_ruled_on(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    new = _traded(snap, x, y, a, b)
    _refresh(lid, snap, new)
    later = new.model_copy(deep=True)
    _move(later, a, None)  # dropped since
    _refresh(lid, new, later)

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()
    assert row["status"] == "stale" and row["numbers"] is None
    assert later.players[a].name in row["status_note"]
    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{row['key']}", json={"ruling": "fair"}, headers=owner)
    assert saved.status_code == 200 and saved.json()["numbers"] is None


# ---- small pieces ---------------------------------------------------------------------------------------


def test_trade_key_is_about_the_deal_not_the_order():
    assert trade_key({1: {"a": 2, "b": 2}, 2: {"c": 1}}) == trade_key({2: {"c": 1}, 1: {"b": 2, "a": 2}})
    assert trade_key({1: {"a": 2}, 2: {"c": 1}}) != trade_key({1: {"c": 2}, 2: {"a": 1}})  # same players, other way round


def test_describing_a_three_team_trade_and_a_player_who_left_the_league(snapshot):
    x, y, z = (t.id for t in snapshot.teams[:3])
    a, b, c = (snapshot.team(t).roster[0].player_id for t in (x, y, z))
    text = describe(snapshot, {x: {a: y}, y: {b: z}, z: {c: x}})["text"]
    assert f"{snapshot.team(x).name} sends {snapshot.players[a].name} to {snapshot.team(y).name}" in text
    assert text.count(";") == 2
    known = {"players": [{"player_id": "gone", "name": "Long Gone"}]}
    assert "Long Gone" in describe(snapshot, {x: {"gone": y}, y: {}}, known)["text"]
    assert "for nothing" in describe(snapshot, {x: {a: y}, y: {}})["text"]


def test_numbers_survive_an_old_evaluation():
    assert numbers(None) is None
    old = numbers({"sides": [{"team_id": 1}], "fairness": {"label": "balanced"}})  # posted before fields were added
    assert old["fairness"] == {"label": "balanced", "reason": ""} and old["sides"][0]["verdict"] is None
