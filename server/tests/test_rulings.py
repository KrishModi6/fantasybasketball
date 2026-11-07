"""The league admin's trade rulings: who can rule, what a ruling records, and what it leaves alone."""

from __future__ import annotations

import base64
import json
import re
import uuid
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.orm import Activity, League, LeagueMember, Snapshot, User
from app.routers import leagues as leagues_router
from app.schemas import EspnConfig, SharedLeague
from app.security import create_token, decrypt_json, encrypt_json
from app.services import rulings_share as share
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


def _league(owner_id: str, snap=None, cfg: dict | None = None, source: str = "espn") -> str:
    with SessionLocal() as db:
        league = League(name="Test", season=2027, source=source, owner_id=owner_id, last_sync_status="ok",
                        connector_config=encrypt_json(cfg or {"league_id": "1"}))
        db.add(league)
        db.flush()
        db.add(LeagueMember(league_id=league.id, user_id=owner_id, role="owner"))
        db.add(Snapshot(league_id=league.id, source=source, data=pack_snapshot(snap or make_snapshot())))
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
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]["key"]

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

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
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


def test_a_ruling_says_when_the_admins_overrides_are_in_its_numbers(client):
    owner_id, owner = _user("Owner")
    member_id, member = _user("Member")
    snap = make_snapshot()
    x, y, a, b = _lopsided(snap)
    lid = _league(owner_id, snap)
    _join(lid, member_id)
    board = f"/api/leagues/{lid}/trades/board"
    assert client.post(board, json=_trade_body(x, y, a, b), headers=owner).status_code == 201
    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
    assert row["numbers"]["adjusted"] is False

    override = f"/api/leagues/{lid}/players/{a}/override"
    assert client.put(override, json={"minutes_pct": 0.15}, headers=owner).status_code == 200
    assert client.post(board, json=_trade_body(x, y, a, b), headers=owner).status_code == 201
    r = client.put(f"/api/leagues/{lid}/trades/rulings/{row['key']}", json={"ruling": "fair"}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["numbers"]["adjusted"] is True

    # Taking the override away afterward doesn't clean the numbers that were made with it.
    assert client.delete(override, headers=owner).status_code == 204
    r = client.put(f"/api/leagues/{lid}/trades/rulings/{row['key']}", json={"ruling": "unfair"}, headers=owner)
    assert r.json()["numbers"]["adjusted"] is True
    [seen] = client.get(f"/api/leagues/{lid}/trades/rulings", headers=member).json()
    assert seen["numbers"]["adjusted"] is True


def test_changing_and_withdrawing_a_ruling(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner).status_code == 201
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]["key"]
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
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]["key"]
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
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]["key"]
    assert client.put(f"/api/leagues/{other}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).status_code == 404


def test_a_ruling_outlives_the_post_it_was_made_on(client):
    owner_id, owner = _user()
    snap = make_snapshot()
    lid = _league(owner_id, snap)
    r = client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(*_lopsided(snap)), headers=owner)
    row = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]
    url = f"/api/leagues/{lid}/trades/rulings/{row['key']}"
    assert client.put(url, json={"ruling": "unfair"}, headers=owner).status_code == 200
    assert client.delete(f"/api/leagues/{lid}/trades/board/{r.json()['id']}", headers=owner).status_code == 204

    [gone] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
    assert gone["status"] == "gone" and gone["numbers"] is None and gone["trade"] == row["trade"]
    changed = client.put(url, json={"ruling": "fair"}, headers=owner).json()
    assert changed["ruling"] == "fair" and changed["numbers"] == row["numbers"]  # nothing new to copy, so the old copy stays
    assert client.delete(url, headers=owner).status_code == 204
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"] == []


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

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
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
    key = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"][0]["key"]
    assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).status_code == 200

    _refresh(lid, snap, _traded(snap, x, y, a, b))
    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
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

    [row] = client.get(f"/api/leagues/{lid}/trades/review", headers=owner).json()["trades"]
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


# ---- reaching other people's desktop apps -----------------------------------------------------------------


class FakeWorker:
    """The rulings routes of cloudflare/ai-proxy/src/worker.mjs, which has its own tests of the same behaviour."""

    def __init__(self):
        self.admins: dict[str, str] = {}
        self.rulings: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self.down = False

    def __call__(self, method, path, admin_key=None, body=None):
        self.calls.append((method, path))
        if self.down:
            return None
        league, trade = re.fullmatch(r"/v1/leagues/([0-9a-f]{40})/rulings(?:/([0-9a-f]{32}))?", path).groups()
        holder = self.admins.get(league)
        if trade is None:
            return httpx.Response(200, json={
                "claimed": holder is not None, "you": holder is not None and holder == admin_key,
                "rulings": [{**v, "key": k} for k, v in self.rulings.get(league, {}).items()]})
        if not admin_key or (holder and holder != admin_key):
            return httpx.Response(403, json={"error": "Only the league's admin can do that."})
        if method == "DELETE":
            self.rulings.get(league, {}).pop(trade, None)
            return httpx.Response(204)
        self.admins.setdefault(league, admin_key)
        self.rulings.setdefault(league, {})[trade] = json.loads(json.dumps(body))
        return httpx.Response(200, json={"ok": True})

    def writes(self) -> list[str]:
        return [method for method, _ in self.calls if method != "GET"]


@pytest.fixture
def worker(monkeypatch):
    """The desktop app's setup: local mode, with a Worker to talk to."""
    settings = get_settings()
    monkeypatch.setattr(settings, "local_mode", True)
    monkeypatch.setattr(settings, "ai_proxy_url", "https://worker.example")
    monkeypatch.setattr(settings, "ai_proxy_token", "client-token")
    fake = FakeWorker()
    monkeypatch.setattr(share, "_call", fake)
    monkeypatch.setattr(share, "_last_pull", {})
    return fake


ESPN = {"league_id": "777"}


def _posted_trade(client, lid: str, who: dict, snap) -> str:
    """Posts the lopsided trade on this copy's own board. Returns its key."""
    x, y, a, b = _lopsided(snap)
    assert client.post(f"/api/leagues/{lid}/trades/board", json=_trade_body(x, y, a, b), headers=who).status_code == 201
    return trade_key({x: {a: y}, y: {b: x}})


def _ruling_feed(client, lid: str, who: dict) -> list[str]:
    return [a["message"] for a in client.get(f"/api/leagues/{lid}/activity", headers=who).json() if a["kind"] == "ruling"]


def test_a_ruling_reaches_a_friends_app(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    friend_id, friend = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    fid = _league(friend_id, snap, cfg={**ESPN, "espn_s2": "the-admins", "swid": "{A}", "shared_by": "the league owner"})
    key = _posted_trade(client, lid, admin, snap)
    _posted_trade(client, fid, friend, snap)  # the same deal on the friend's own board
    evaluation = client.get(f"/api/leagues/{fid}/trades/board", headers=friend).json()[0]["evaluation"]

    assert client.get(f"/api/leagues/{lid}/trades/review", headers=admin).json()["reach"] == "league"
    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "unfair", "note": "too lopsided"}, headers=admin)
    assert saved.status_code == 200 and saved.json()["pending"] is False

    # What the friend's app shows: the admin's call, with the numbers as they stood, and its own verdict untouched.
    share._last_pull.clear()
    [seen] = client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json()
    assert seen["ruling"] == "unfair" and seen["note"] == "too lopsided" and seen["numbers"] == saved.json()["numbers"]
    assert seen["trade"]["text"] == saved.json()["trade"]["text"]
    assert seen["decided_at"] == saved.json()["decided_at"]
    post = client.get(f"/api/leagues/{fid}/trades/board", headers=friend).json()[0]
    assert post["ruling"] == "unfair" and post["evaluation"] == evaluation
    assert _ruling_feed(client, fid, friend)[0].startswith("League admin ruled a trade not fair:")
    assert worker.writes() == ["PUT"]  # only the admin's copy sent anything

    # Reading again changes nothing, and doesn't repeat itself in the feed.
    share._last_pull.clear()
    client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend)
    assert len(_ruling_feed(client, fid, friend)) == 1

    # A new note arrives quietly; a changed call is news; so is a withdrawn one.
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "unfair", "note": "still lopsided"}, headers=admin)
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json()[0]["note"] == "still lopsided"
    assert len(_ruling_feed(client, fid, friend)) == 1
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin)
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json()[0]["ruling"] == "fair"
    assert _ruling_feed(client, fid, friend)[0].startswith("League admin changed a ruling to fair:")
    assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin).status_code == 204
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json() == []
    assert client.get(f"/api/leagues/{fid}/trades/board", headers=friend).json()[0]["ruling"] is None
    assert _ruling_feed(client, fid, friend)[0].startswith("League admin withdrew a ruling:")


def test_a_copy_from_an_invite_code_never_writes(client, worker):
    snap = make_snapshot()
    friend_id, friend = _user("You")
    fid = _league(friend_id, snap, cfg={**ESPN, "shared_by": "the league owner"})
    key = _posted_trade(client, fid, friend, snap)
    assert client.get(f"/api/leagues/{fid}/trades/review", headers=friend).status_code == 403
    assert client.put(f"/api/leagues/{fid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=friend).status_code == 403
    assert client.delete(f"/api/leagues/{fid}/trades/rulings/{key}", headers=friend).status_code == 403
    assert client.get(f"/api/leagues/{fid}/trades/board", headers=friend).status_code == 200
    assert worker.writes() == [] and worker.admins == {}


def test_connecting_the_league_by_hand_doesnt_get_past_the_admin(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    rival_id, rival = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    rid = _league(rival_id, snap, cfg=ESPN)  # looks like any other league on the rival's computer
    key = _posted_trade(client, lid, admin, snap)
    rival_key = _posted_trade(client, rid, rival, snap)
    assert client.get(f"/api/leagues/{rid}", headers=rival).json()["me"]["is_admin"] is True

    assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin).status_code == 200
    # The Worker is what stops this: their engine is on their own computer.
    refused = client.put(f"/api/leagues/{rid}/trades/rulings/{rival_key}", json={"ruling": "unfair"}, headers=rival)
    assert refused.status_code == 403 and "already this league's admin" in refused.json()["detail"]
    [held] = next(iter(worker.rulings.values())).values(),
    assert list(held)[0]["ruling"] == "fair"  # the admin's call stands

    # From then on that copy reads like any friend's.
    assert client.get(f"/api/leagues/{rid}", headers=rival).json()["me"]["is_admin"] is False
    assert client.get(f"/api/leagues/{rid}/trades/review", headers=rival).status_code == 403
    share._last_pull.clear()
    assert [r["ruling"] for r in client.get(f"/api/leagues/{rid}/trades/rulings", headers=rival).json()] == ["fair"]


def test_loading_the_board_is_enough_to_learn_the_league_has_an_admin(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    rival_id, rival = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    rid = _league(rival_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin)
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{rid}/trades/board", headers=rival).status_code == 200
    assert client.get(f"/api/leagues/{rid}", headers=rival).json()["me"]["is_admin"] is False
    # And the admin's own copy stays the admin's.
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=admin).status_code == 200
    assert client.get(f"/api/leagues/{lid}", headers=admin).json()["me"]["is_admin"] is True


def test_a_ruling_made_offline_goes_out_later(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)

    worker.down = True
    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "unfair"}, headers=admin)
    assert saved.status_code == 200 and saved.json()["pending"] is True
    share._last_pull.clear()
    row = client.get(f"/api/leagues/{lid}/trades/review", headers=admin).json()["trades"][0]
    assert row["ruling"]["pending"] is True

    worker.down = False
    share._last_pull.clear()
    row = client.get(f"/api/leagues/{lid}/trades/review", headers=admin).json()["trades"][0]
    assert row["ruling"]["pending"] is False
    assert next(iter(worker.rulings.values()))[key]["ruling"] == "unfair"


def test_a_worker_without_the_rulings_routes_just_leaves_them_pending(client, worker, monkeypatch):
    # Rulings ship in the app before or after the Worker deploy: neither order should lose one.
    snap = make_snapshot()
    admin_id, admin = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)
    monkeypatch.setattr(share, "_call", lambda *a, **k: httpx.Response(404, json={"error": "Not found."}))
    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin)
    assert saved.status_code == 200 and saved.json()["pending"] is True
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=admin).status_code == 200
    assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin).status_code == 204  # never left this computer


def test_withdrawing_has_to_reach_the_league(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)
    assert client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin).status_code == 200

    worker.down = True
    refused = client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin)
    assert refused.status_code == 503 and "still stands" in refused.json()["detail"]
    assert len(client.get(f"/api/leagues/{lid}/trades/rulings", headers=admin).json()) == 1

    worker.down = False
    assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin).status_code == 204
    assert next(iter(worker.rulings.values())) == {}


def test_a_ruling_that_never_left_this_computer_can_always_be_withdrawn(client, worker):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)
    worker.down = True
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin)
    assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin).status_code == 204


def test_a_broken_answer_from_the_worker_doesnt_break_the_page(client, worker, monkeypatch):
    snap = make_snapshot()
    owner_id, owner = _user("You")
    lid = _league(owner_id, snap, cfg=ESPN)
    _posted_trade(client, lid, owner, snap)
    monkeypatch.setattr(share, "_call", lambda *a, **k: httpx.Response(200, json={"surprise": True}))
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{lid}/trades/board", headers=owner).status_code == 200


def test_what_each_kind_of_league_reaches(client, worker, monkeypatch):
    snap = make_snapshot()
    owner_id, owner = _user("You")
    espn = _league(owner_id, snap, cfg=ESPN)
    other = _league(owner_id, snap, source="file", cfg={"spec": {}})
    assert client.get(f"/api/leagues/{espn}/trades/review", headers=owner).json()["reach"] == "league"
    assert client.get(f"/api/leagues/{other}/trades/review", headers=owner).json()["reach"] == "this_copy"
    key = _posted_trade(client, other, owner, snap)
    assert client.put(f"/api/leagues/{other}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).json()["pending"] is False
    assert worker.writes() == []  # a file league has no id another copy could match

    monkeypatch.setattr(get_settings(), "local_mode", False)  # a shared server: everyone reads the same database
    assert client.get(f"/api/leagues/{espn}/trades/review", headers=owner).json()["reach"] == "members"
    key = _posted_trade(client, espn, owner, snap)
    worker.calls.clear()
    assert client.put(f"/api/leagues/{espn}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).json()["pending"] is False
    assert worker.calls == []


def test_the_admin_key_stays_on_the_admins_computer(client, worker, monkeypatch):
    snap = make_snapshot()
    admin_id, admin = _user("You")
    lid = _league(admin_id, snap, cfg=ESPN)
    key = _posted_trade(client, lid, admin, snap)
    sent = []
    real = worker.__call__
    monkeypatch.setattr(share, "_call", lambda m, p, k=None, b=None: (sent.append((k, b)), real(m, p, k, b))[1])
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=admin)
    client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "unfair"}, headers=admin)

    with SessionLocal() as db:
        secret = share._stored_key(db, admin_id)
    assert secret and [k for k, _ in sent] == [secret, secret]  # the same key each time
    assert secret not in json.dumps([b for _, b in sent])
    code = client.post(f"/api/leagues/{lid}/share-code", json={}, headers=admin).json()["code"]
    assert secret not in zlib.decompress(base64.urlsafe_b64decode(code[4:] + "=" * (-len(code[4:]) % 4))).decode()
    assert secret not in client.get(f"/api/leagues/{lid}", headers=admin).text

    # It shares a row with the user's OpenAI key, and removing that mustn't lose it.
    assert client.delete("/api/ai/key", headers=admin).status_code == 200
    with SessionLocal() as db:
        assert share._stored_key(db, admin_id) == secret


# ---- admin codes: for an admin whose own copy can't claim the league --------------------------------------


CODE = "the-admin-code_0123456789"
FROM_INVITE = {**ESPN, "espn_s2": "the-admins", "swid": "{A}", "shared_by": "the league owner"}


def _locked(worker) -> None:
    """The league held by a code, before any copy has entered it."""
    worker.admins[share.league_key(League(source="espn", season=2027, connector_config=encrypt_json(ESPN)))] = CODE


def _enter(client, lid: str, who: dict, code: str = CODE):
    return client.post(f"/api/leagues/{lid}/trades/admin-code", json={"code": code}, headers=who)


def _is_admin(client, lid: str, who: dict) -> bool:
    return client.get(f"/api/leagues/{lid}", headers=who).json()["me"]["is_admin"]


def test_the_admin_code_makes_a_copy_from_an_invite_code_the_admin(client, worker):
    snap = make_snapshot()
    _locked(worker)
    admin_id, admin = _user("You")
    friend_id, friend = _user("You")
    lid = _league(admin_id, snap, cfg=FROM_INVITE)
    fid = _league(friend_id, snap, cfg=FROM_INVITE)
    key = _posted_trade(client, lid, admin, snap)

    # What 0.5.9 left behind on the admin's copy: told someone else holds the league.
    share._last_pull.clear()
    client.get(f"/api/leagues/{lid}/trades/rulings", headers=admin)
    assert _is_admin(client, lid, admin) is False

    assert _enter(client, lid, admin, f"  {CODE}\n").json() == {"is_admin": True}  # pasted with stray whitespace
    assert _is_admin(client, lid, admin) is True
    assert client.get(f"/api/leagues/{lid}/trades/review", headers=admin).status_code == 200

    # It rules under the code, and stays the admin after the Worker's next answer.
    saved = client.put(f"/api/leagues/{lid}/trades/rulings/{key}", json={"ruling": "unfair"}, headers=admin)
    assert saved.status_code == 200 and saved.json()["pending"] is False
    share._last_pull.clear()
    client.get(f"/api/leagues/{lid}/trades/rulings", headers=admin)
    assert _is_admin(client, lid, admin) is True
    assert len(client.get(f"/api/leagues/{lid}/trades/rulings", headers=admin).json()) == 1

    # Everyone else just reads it.
    share._last_pull.clear()
    assert [r["ruling"] for r in client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json()] == ["unfair"]
    assert _is_admin(client, fid, friend) is False
    assert client.put(f"/api/leagues/{fid}/trades/rulings/{key}", json={"ruling": "fair"}, headers=friend).status_code == 403

    assert client.delete(f"/api/leagues/{lid}/trades/rulings/{key}", headers=admin).status_code == 204
    share._last_pull.clear()
    assert client.get(f"/api/leagues/{fid}/trades/rulings", headers=friend).json() == []


@pytest.mark.parametrize("wrong", ["not-the-code", "  ", "two words", "café", "with\nnewline"])
def test_a_wrong_code_changes_nothing(client, worker, wrong):
    _locked(worker)
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    refused = _enter(client, lid, owner, wrong)
    assert refused.status_code == 403 and "isn't right" in refused.json()["detail"]
    assert _is_admin(client, lid, owner) is False
    with SessionLocal() as db:
        assert "admin_key" not in decrypt_json(db.get(League, lid).connector_config)


def test_an_unclaimed_league_has_no_right_code(client, worker):
    # Otherwise any string would "work", and hold the league with it on the first ruling.
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    assert _enter(client, lid, owner).status_code == 403
    assert worker.admins == {} and _is_admin(client, lid, owner) is False


def test_a_code_cant_be_checked_while_the_worker_is_down(client, worker):
    _locked(worker)
    worker.down = True
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    answer = _enter(client, lid, owner)
    assert answer.status_code == 503 and "Try again" in answer.json()["detail"]
    assert _is_admin(client, lid, owner) is False


def test_only_a_copys_owner_can_enter_a_code_and_only_where_it_can_reach_the_league(client, worker, monkeypatch):
    _locked(worker)
    owner_id, owner = _user("You")
    member_id, member = _user()
    lid = _league(owner_id, cfg=FROM_INVITE)
    _join(lid, member_id)
    assert _enter(client, lid, member).status_code == 403
    assert client.post(f"/api/leagues/{lid}/trades/admin-code", json={"code": CODE}).status_code == 401

    other = _league(owner_id, source="file", cfg={"spec": {}})
    assert _enter(client, other, owner).status_code == 400
    monkeypatch.setattr(get_settings(), "local_mode", False)
    assert _enter(client, lid, owner).status_code == 400


def test_the_code_survives_new_cookies_and_never_leaves_the_copy(client, worker):
    _locked(worker)
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    assert _enter(client, lid, owner).status_code == 200

    body = {"espn": {"league_id": "777", "espn_s2": "my-own", "swid": "{ME}"}}
    assert client.patch(f"/api/leagues/{lid}", json=body, headers=owner).status_code == 200
    assert _is_admin(client, lid, owner) is True

    shared = client.post(f"/api/leagues/{lid}/share-code", json={}, headers=owner).json()["code"]
    assert CODE not in zlib.decompress(base64.urlsafe_b64decode(shared[4:] + "=" * (-len(shared[4:]) % 4))).decode()
    assert CODE not in client.get(f"/api/leagues/{lid}", headers=owner).text
    with SessionLocal() as db:
        assert decrypt_json(db.get(League, lid).connector_config)["admin_key"] == CODE
        assert CODE.encode() not in db.get(League, lid).connector_config  # encrypted at rest


def test_the_code_survives_reconnecting_with_an_invite_code(client, worker, monkeypatch):
    # A copy that can't read ESPN switches to the login in any code pasted into it. That mustn't cost the admin the league.
    _locked(worker)
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    assert _enter(client, lid, owner).status_code == 200
    with SessionLocal() as db:
        db.get(League, lid).last_sync_status = "error"
        db.commit()
    monkeypatch.setattr(leagues_router, "_first_sync", lambda *a, **k: None)
    login = EspnConfig(league_id=ESPN["league_id"], espn_s2="fresh", swid="{F}")
    invite = leagues_router._pack_code(SharedLeague(source="espn", name="Test", season=2027, espn=login))

    assert client.post("/api/join-code", json={"code": invite}, headers=owner).status_code == 201
    with SessionLocal() as db:
        cfg = decrypt_json(db.get(League, lid).connector_config)
    assert cfg["espn_s2"] == "fresh" and cfg["admin_key"] == CODE
    assert _is_admin(client, lid, owner) is True


def test_a_code_doesnt_touch_this_copys_key_for_other_leagues(client, worker):
    # The key a copy makes for itself covers every league it connected by hand, so the code can't replace it.
    snap = make_snapshot()
    _locked(worker)
    owner_id, owner = _user("You")
    mine = _league(owner_id, snap, cfg={"league_id": "888"})
    key = _posted_trade(client, mine, owner, snap)
    assert client.put(f"/api/leagues/{mine}/trades/rulings/{key}", json={"ruling": "fair"}, headers=owner).status_code == 200
    with SessionLocal() as db:
        own_key = share._stored_key(db, owner_id)
    lid = _league(owner_id, snap, cfg=FROM_INVITE)
    assert _enter(client, lid, owner).status_code == 200

    with SessionLocal() as db:
        assert share._stored_key(db, owner_id) == own_key
    assert client.put(f"/api/leagues/{mine}/trades/rulings/{key}", json={"ruling": "unfair"}, headers=owner).status_code == 200
    assert sorted(worker.admins.values()) == sorted([CODE, own_key])


def test_a_new_computer_takes_on_the_rulings_already_made(client, worker):
    snap = make_snapshot()
    _locked(worker)
    old_id, old = _user("You")
    new_id, new = _user("You")
    was = _league(old_id, snap, cfg=FROM_INVITE)
    assert _enter(client, was, old).status_code == 200
    key = _posted_trade(client, was, old, snap)
    assert client.put(f"/api/leagues/{was}/trades/rulings/{key}", json={"ruling": "fair"}, headers=old).status_code == 200

    now = _league(new_id, snap, cfg=FROM_INVITE)
    assert _enter(client, now, new).status_code == 200
    assert [r["ruling"] for r in client.get(f"/api/leagues/{now}/trades/rulings", headers=new).json()] == ["fair"]


def test_a_code_that_stops_holding_the_league_stops_counting(client, worker):
    _locked(worker)
    owner_id, owner = _user("You")
    lid = _league(owner_id, cfg=FROM_INVITE)
    assert _enter(client, lid, owner).status_code == 200
    for league in worker.admins:
        worker.admins[league] = "a-newer-code"
    share._last_pull.clear()
    client.get(f"/api/leagues/{lid}/trades/rulings", headers=owner)
    assert _is_admin(client, lid, owner) is False
    assert _enter(client, lid, owner, "a-newer-code").status_code == 200
    assert _is_admin(client, lid, owner) is True


def test_league_key_matches_across_copies_and_hides_the_league():
    a = League(source="espn", season=2027, connector_config=encrypt_json({"league_id": "12345"}))
    b = League(source="espn", season=2027, connector_config=encrypt_json({"league_id": "12345", "shared_by": "them"}))
    assert share.league_key(a) == share.league_key(b) and "12345" not in share.league_key(a)
    assert re.fullmatch(r"[0-9a-f]{40}", share.league_key(a))
    assert share.league_key(League(source="espn", season=2028, connector_config=a.connector_config)) != share.league_key(a)
    assert share.league_key(League(source="demo", season=2027, connector_config=a.connector_config)) is None
