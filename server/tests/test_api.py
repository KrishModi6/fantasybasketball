"""End-to-end API test against a demo league (needs network access to ESPN on first run; cached after)."""

from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ.setdefault("FBB_DATABASE_URL", f"sqlite:///{_tmp}/test.db")  # conftest may supply Postgres
os.environ["FBB_CRON_TOKEN"] = "test-cron-token"
os.environ["FBB_SCHEDULER_ENABLED"] = "false"
os.environ["FBB_SECRET_KEY"] = "test-secret-key-not-for-production-use-0123456789"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(c, email, name):
    r = c.post("/api/auth/register", json={"email": email, "password": "correct horse battery", "display_name": name})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_full_flow(client):
    owner = _auth(client, "owner@example.com", "Owner")
    r = client.post("/api/leagues", json={"source": "demo", "name": "Test League"}, headers=owner)
    assert r.status_code == 201, r.text
    lg = r.json()
    lid = lg["id"]
    assert lg["settings"]["scoring"]["points"]["STL"] == 4
    assert len(lg["teams"]) == 10
    listed = client.get("/api/leagues", headers=owner).json()
    assert [(x["member_count"], x["team_count"]) for x in listed] == [(1, 10)]

    r = client.get(f"/api/leagues/{lid}/rankings", headers=owner)
    assert r.status_code == 200, r.text
    teams = r.json()["teams"]
    assert [t["rank"] for t in teams] == list(range(1, 11))
    assert abs(sum(t["title_odds"] for t in teams) - 1) < 0.02
    assert teams[0]["explanation"]["summary"]

    wk = client.get(f"/api/leagues/{lid}/weekly", headers=owner).json()
    assert wk["scoring"] == "points" and wk["weeks"] and len(wk["teams"]) == 10
    assert {w["matchup_period"] for w in wk["weeks"]} == {c["matchup_period"] for c in wk["teams"][0]["weeks"]}
    future = [i for i, w in enumerate(wk["weeks"]) if not w["current"] and not w["playoffs"]]
    assert all(t["weeks"][i]["proj"] > 0 for t in wk["teams"] for i in future)
    scheduled = [c for t in wk["teams"] for c in t["weeks"] if c["opponent_id"] is not None]
    assert scheduled and all(0 <= c["win_prob"] <= 1 for c in scheduled)

    tid = teams[0]["team_id"]
    team = client.get(f"/api/leagues/{lid}/teams/{tid}", headers=owner)
    assert team.status_code == 200
    assert all("today_fp" in r and "today_min" in r for r in team.json()["roster"])
    assert "live_window" in lg["settings"]
    rep = client.get(f"/api/leagues/{lid}/teams/{tid}/report", headers=owner).json()
    assert "weaknesses" in rep and "moves" in rep
    lu = client.get(f"/api/leagues/{lid}/teams/{tid}/lineup?days=3", headers=owner).json()
    assert len(lu["days"]) >= 1

    pl = client.get(f"/api/leagues/{lid}/players?status=fa&limit=20", headers=owner).json()
    assert pl["players"] and all(p["fantasy_team_id"] is None for p in pl["players"])
    pid = pl["players"][0]["id"]
    det = client.get(f"/api/leagues/{lid}/players/{pid}", headers=owner).json()
    assert det["sources"] and "explanation" in det

    team = client.get(f"/api/leagues/{lid}/teams/{tid}", headers=owner).json()
    other = next(t for t in teams if t["team_id"] != tid)["team_id"]
    other_roster = client.get(f"/api/leagues/{lid}/teams/{other}", headers=owner).json()["roster"]
    body = {"team_a": tid, "team_b": other, "a_gives": [team["roster"][0]["id"]], "b_gives": [other_roster[0]["id"]]}
    ev = client.post(f"/api/leagues/{lid}/trades/evaluate", json=body, headers=owner)
    assert ev.status_code == 200, ev.text
    assert len(ev.json()["sides"]) == 2 and ev.json()["math"]
    sug = client.get(f"/api/leagues/{lid}/trades/suggestions?team_id={tid}", headers=owner)
    assert sug.status_code == 200, sug.text
    sug = client.get(f"/api/leagues/{lid}/trades/suggestions?team_id={tid}&max_give=2&max_get=2&exact=true", headers=owner)
    assert sug.status_code == 200, sug.text
    assert set(sug.json()["checked"]) == {"2-for-2"}
    assert all(len(s["give"]) == len(s["get"]) == 2 for s in sug.json()["suggestions"])
    even = client.get(f"/api/leagues/{lid}/trades/suggestions?team_id={tid}&sizes=1-1,2-2", headers=owner).json()
    assert set(even["checked"]) <= {"1-for-1", "2-for-2"}
    assert all(len(s["give"]) == len(s["get"]) for s in even["suggestions"])
    assert client.get(f"/api/leagues/{lid}/trades/suggestions?team_id={tid}&sizes=2for1", headers=owner).status_code == 422
    three = client.get(f"/api/leagues/{lid}/trades/suggestions?team_id={tid}&sizes=3-3", headers=owner).json()
    assert set(three["checked"]) == {"3-for-3"}
    assert all(len(s["give"]) == len(s["get"]) == 3 for s in three["suggestions"])

    want = other_roster[0]["id"]
    off = client.get(f"/api/leagues/{lid}/trades/offers?team_id={tid}&target={want}&give=1,2", headers=owner)
    assert off.status_code == 200, off.text
    off = off.json()
    assert off["partner_id"] == other and off["get"] == [want] and off["get_names"] == [other_roster[0]["name"]]
    assert set(off["checked"]) <= {"1-for-1", "2-for-1"}
    assert all(len(o["give_names"]) == len(o["give"]) <= 2 for o in off["offers"] + off["closest"])
    mine = client.get(f"/api/leagues/{lid}/trades/offers?team_id={tid}&target={team['roster'][0]['id']}", headers=owner)
    assert mine.status_code == 422 and "Pick a player from another team" in mine.json()["detail"]
    assert client.get(f"/api/leagues/{lid}/trades/offers?team_id={tid}", headers=owner).status_code == 422
    assert client.get(f"/api/leagues/{lid}/trades/offers?team_id={tid}&target={want}&give=4", headers=owner).status_code == 422
    assert all(o["get_names"] == off["get_names"] for o in off["offers"])

    shop = team["roster"][0]["id"]
    ask = client.get(f"/api/leagues/{lid}/trades/asks?team_id={tid}&player={shop}&partner_id={other}&get=1,2", headers=owner)
    assert ask.status_code == 200, ask.text
    ask = ask.json()
    assert ask["partner_id"] == other and ask["give"] == [shop] and ask["give_names"] == [team["roster"][0]["name"]]
    assert set(ask["checked"]) <= {"1-for-1", "1-for-2"}
    assert all(o["partner_id"] == other and len(o["get_names"]) == len(o["get"]) <= 2 for o in ask["offers"] + ask["closest"])
    anyone = client.get(f"/api/leagues/{lid}/trades/asks?team_id={tid}&player={shop}&get=1", headers=owner).json()
    assert anyone["partner_id"] is None and all(o["partner_id"] != tid for o in anyone["offers"])
    theirs = client.get(f"/api/leagues/{lid}/trades/asks?team_id={tid}&player={want}", headers=owner)
    assert theirs.status_code == 422 and "Pick one of your own players" in theirs.json()["detail"]
    assert client.get(f"/api/leagues/{lid}/trades/asks?team_id={tid}", headers=owner).status_code == 422

    rot = client.get(f"/api/leagues/{lid}/trades/three-team?team_id={tid}&partner_id={other}", headers=owner)
    assert rot.status_code == 200, rot.text
    rot = rot.json()
    assert rot["checked"]["3-team"] > 0
    assert all(r["teams"][0] == tid and other in r["teams"] and len(r["moves"]) == 3 and r["give_names"] and r["get_names"]
               for r in rot["suggestions"])
    assert client.get(f"/api/leagues/{lid}/trades/three-team?team_id={tid}&partner_id={tid}", headers=owner).status_code == 422

    # Matchups + simulator
    wk = client.get(f"/api/leagues/{lid}/matchups", headers=owner).json()
    assert wk["weeks"] and wk["matchups"]
    m0 = wk["matchups"][0]
    sim = client.post(f"/api/leagues/{lid}/matchups/simulate", headers=owner, json={
        "team_a": m0["home_id"], "team_b": m0["away_id"], "matchup_period": wk["matchup_period"], "sims": 2000})
    assert sim.status_code == 200, sim.text
    body_sim = sim.json()
    assert abs(sum(t["win_prob"] for t in body_sim["teams"]) + body_sim["tie_prob"] - 1) < 1e-9
    bad = client.post(f"/api/leagues/{lid}/matchups/simulate", headers=owner, json={
        "team_a": m0["home_id"], "team_b": m0["home_id"], "matchup_period": wk["matchup_period"]})
    assert bad.status_code == 422

    # Invite flow
    inv = client.post(f"/api/leagues/{lid}/invites", json={"expires_in_days": 7}, headers=owner).json()
    member = _auth(client, "member@example.com", "Member")
    assert client.get(f"/api/leagues/{lid}", headers=member).status_code == 404  # not a member yet
    prev = client.get(f"/api/invites/{inv['code']}").json()
    assert prev["league_name"] == "Test League"
    assert client.post(f"/api/invites/{inv['code']}/accept", json={"team_id": other}, headers=member).status_code == 200
    me = client.get(f"/api/leagues/{lid}", headers=member).json()["me"]
    assert me == {"role": "member", "team_id": other}
    # Members can't manage invites
    assert client.post(f"/api/leagues/{lid}/invites", json={}, headers=member).status_code == 403
    # Trade board + activity
    assert client.post(f"/api/leagues/{lid}/trades/board", json={**body, "note": "thoughts?"}, headers=member).status_code == 201
    assert len(client.get(f"/api/leagues/{lid}/trades/board", headers=owner).json()) == 1
    acts = client.get(f"/api/leagues/{lid}/activity", headers=owner).json()
    assert any(a["kind"] == "trade" for a in acts)
    # Bounded override is applied and capped
    r = client.put(f"/api/leagues/{lid}/players/{pid}/override", json={"minutes_pct": 0.5}, headers=member)
    assert r.status_code == 422
    r = client.put(f"/api/leagues/{lid}/players/{pid}/override", json={"minutes_pct": 0.1, "note": "starter now"},
                   headers=member)
    assert r.status_code == 200
    det2 = client.get(f"/api/leagues/{lid}/players/{pid}", headers=owner).json()
    assert any(adj["kind"] == "manual" for adj in det2["adjustments"])
    assert det2["mpg"] > det["mpg"]


def test_league_code_join(client):
    """A league code lets someone on another copy of Courtside connect the same league in one step."""
    owner = _auth(client, "code-owner@example.com", "Code Owner")
    lg = client.post("/api/leagues", json={"source": "demo", "name": "Code League", "team_count": 8}, headers=owner).json()
    team = lg["teams"][3]["id"]
    r = client.post(f"/api/leagues/{lg['id']}/share-code", json={"team_id": team}, headers=owner)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code.startswith("CS1.") and r.json()["includes_login"] is False

    friend = _auth(client, "code-friend@example.com", "Friend")
    wrapped = "Join us! " + "\n".join(code[i:i + 60] for i in range(0, len(code), 60))  # as a chat app wraps it
    r = client.post("/api/join-code", json={"code": wrapped}, headers=friend)
    assert r.status_code == 201, r.text
    joined = r.json()
    assert joined["id"] != lg["id"] and joined["name"] == "Code League" and len(joined["teams"]) == 8
    assert joined["me"] == {"role": "owner", "team_id": team}  # their own copy, with the team picked for them
    assert joined["connector"]["shared_by"] == "Code Owner"

    # Members can't make codes; damaged codes get a plain message.
    assert client.post(f"/api/leagues/{lg['id']}/share-code", json={}, headers=friend).status_code == 404
    bad = client.post("/api/join-code", json={"code": code[:-12]}, headers=friend)
    assert bad.status_code == 422 and "incomplete" in bad.json()["detail"]
    assert client.post("/api/join-code", json={"code": "7KQ2MZ4P-not-a-code"}, headers=friend).status_code == 422


def test_own_openai_key(client, monkeypatch):
    import app.routers.ai as ai_router
    from app.services.ai import AIError

    def fake_check(key):
        if key.endswith("bad"):
            raise AIError(400, "OpenAI didn't accept that key.")

    monkeypatch.setattr(ai_router, "check_openai_key", fake_check)
    user = _auth(client, "keyholder@example.com", "Key Holder")
    st = client.get("/api/ai/status", headers=user).json()
    assert st["own_key"] is False
    assert client.put("/api/ai/key", json={"api_key": "not-a-key-at-all-xxxxxxxx"}, headers=user).status_code == 422
    assert client.put("/api/ai/key", json={"api_key": "sk-test-0123456789abcdefbad"}, headers=user).status_code == 400
    r = client.put("/api/ai/key", json={"api_key": " sk-test-0123456789abcdefWXYZ "}, headers=user)
    assert r.status_code == 200, r.text
    assert r.json().items() >= {"own_key": True, "own_key_hint": "WXYZ", "mode": "direct", "enabled": True}.items()
    other = _auth(client, "nokey@example.com", "No Key")
    assert client.get("/api/ai/status", headers=other).json()["own_key"] is False  # keys are per user
    r = client.delete("/api/ai/key", headers=user)
    assert r.json()["own_key"] is False and r.json()["own_key_hint"] is None

def test_espn_login_claims_its_own_team(client):
    """An ESPN league connected with your own cookies opens on your team, with nothing to pick."""
    from app.db import SessionLocal
    from app.orm import League, LeagueMember, User
    from app.security import encrypt_json
    from app.services.sync import claim_own_team
    from factories import make_snapshot

    snap = make_snapshot()
    snap.teams[2].owner_ids = ["{ABCDEF12-3456-7890-ABCD-EF1234567890}"]
    db = SessionLocal()
    try:
        user = User(email="espn-owner@example.com", display_name="Espn Owner", password_hash="!")
        db.add(user)
        db.flush()

        def connect(cfg):
            lg = League(name="ESPN", season=2027, source="espn", owner_id=user.id, connector_config=encrypt_json(cfg))
            db.add(lg)
            db.flush()
            me = LeagueMember(league_id=lg.id, user_id=user.id, role="owner")
            db.add(me)
            db.flush()
            return lg, me

        # The SWID cookie may be pasted without braces or in lower case.
        lg, me = connect({"league_id": "1", "espn_s2": "x", "swid": "abcdef12-3456-7890-abcd-ef1234567890"})
        claim_own_team(db, lg, snap)
        assert me.team_id == snap.teams[2].id
        me.team_id = snap.teams[0].id  # a team picked by hand is never replaced
        claim_own_team(db, lg, snap)
        assert me.team_id == snap.teams[0].id

        # A copy joined with an invite code runs on the sender's cookies, so their team isn't yours.
        lg2, me2 = connect({"league_id": "1", "espn_s2": "x", "swid": "{ABCDEF12-3456-7890-ABCD-EF1234567890}",
                            "shared_by": "Pal"})
        claim_own_team(db, lg2, snap)
        assert me2.team_id is None
    finally:
        db.rollback()
        db.close()

def test_board_follows_the_league(client):
    """Board posts are re-scored after each refresh and flagged once a roster move makes them impossible."""
    from app.db import SessionLocal
    from app.orm import League, Snapshot
    from app.services.analysis import invalidate, latest_snapshot_row, pack_snapshot, unpack_snapshot
    from app.services.board import refresh_board

    owner = _auth(client, "board-owner@example.com", "Board Owner")
    lg = client.post("/api/leagues", json={"source": "demo", "name": "Board League", "team_count": 8}, headers=owner).json()
    a, b = lg["teams"][0]["id"], lg["teams"][1]["id"]
    ra = client.get(f"/api/leagues/{lg['id']}/teams/{a}", headers=owner).json()["roster"]
    rb = client.get(f"/api/leagues/{lg['id']}/teams/{b}", headers=owner).json()["roster"]
    trade = {"team_a": a, "team_b": b, "a_gives": [ra[0]["id"]], "b_gives": [rb[0]["id"]], "note": "?"}
    assert client.post(f"/api/leagues/{lg['id']}/trades/board", json=trade, headers=owner).status_code == 201

    db = SessionLocal()
    try:
        league = db.get(League, lg["id"])
        assert refresh_board(db, league) == 1
        post = client.get(f"/api/leagues/{lg['id']}/trades/board", headers=owner).json()[0]
        assert post["evaluation"]["status"] == "open" and post["evaluation"]["as_of"]

        snap = unpack_snapshot(latest_snapshot_row(db, league.id).data)  # team A drops the player it offered
        snap.team(a).roster = [e for e in snap.team(a).roster if e.player_id != ra[0]["id"]]
        db.add(Snapshot(league_id=league.id, source=snap.source, data=pack_snapshot(snap)))
        db.commit()
        invalidate(league.id)
        refresh_board(db, league)
    finally:
        db.close()
    post = client.get(f"/api/leagues/{lg['id']}/trades/board", headers=owner).json()[0]
    assert post["evaluation"]["status"] == "stale"
    assert post["evaluation"]["status_note"] == f"{ra[0]['name']} is now a free agent."

def test_three_team_trade(client):
    from app.db import SessionLocal
    from app.orm import League
    from app.services.board import refresh_board

    owner = _auth(client, "three-team@example.com", "Three Team")
    lg = client.post("/api/leagues", json={"source": "demo", "name": "Three Team League", "team_count": 8}, headers=owner).json()
    lid = lg["id"]
    a, b, c = (t["id"] for t in lg["teams"][:3])
    ra, rb, rc = (client.get(f"/api/leagues/{lid}/teams/{t}", headers=owner).json()["roster"] for t in (a, b, c))
    trade = {"team_a": a, "team_b": b, "team_c": c, "a_gives": [ra[0]["id"]], "b_gives": [rb[0]["id"]],
             "c_gives": [rc[0]["id"]], "send_to": {ra[0]["id"]: b, rb[0]["id"]: c, rc[0]["id"]: a}}
    ev = client.post(f"/api/leagues/{lid}/trades/evaluate", json=trade, headers=owner)
    assert ev.status_code == 200, ev.text
    assert [s["team_id"] for s in ev.json()["sides"]] == [a, b, c]
    assert ev.json()["sides"][0]["receives"] == [rc[0]["id"]]
    assert ev.json()["sides"][0]["before_playoff_odds"] is not None

    no_dest = {**trade, "send_to": {ra[0]["id"]: b, rb[0]["id"]: c}}
    r = client.post(f"/api/leagues/{lid}/trades/evaluate", json=no_dest, headers=owner)
    assert r.status_code == 422 and r.json()["detail"] == f"Pick which team gets {rc[0]['name']}"
    r = client.post(f"/api/leagues/{lid}/trades/evaluate", json={**trade, "team_c": a}, headers=owner)
    assert r.status_code == 422 and r.json()["detail"] == "Pick three different teams in this league"

    assert client.post(f"/api/leagues/{lid}/trades/board", json={**trade, "note": "3-way"}, headers=owner).status_code == 201
    post = client.get(f"/api/leagues/{lid}/trades/board", headers=owner).json()[0]
    assert post["teams"] == [a, b, c] and len(post["evaluation"]["sides"]) == 3
    assert {"player_id": rc[0]["id"], "from_team": c, "to_team": a} in post["moves"]
    db = SessionLocal()
    try:
        assert refresh_board(db, db.get(League, lid)) == 1
    finally:
        db.close()
    post = client.get(f"/api/leagues/{lid}/trades/board", headers=owner).json()[0]
    assert post["evaluation"]["status"] == "open" and len(post["evaluation"]["sides"]) == 3
    feed = client.get(f"/api/leagues/{lid}/activity", headers=owner).json()
    assert any("three-team trade idea" in f["message"] for f in feed)


def test_auth_errors(client):
    assert client.get("/api/leagues").status_code == 401
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert r.status_code == 401


def test_cron_endpoint_requires_token(client):
    assert client.post("/api/cron/sync").status_code == 404
    assert client.post("/api/cron/sync", headers={"X-Cron-Token": "wrong"}).status_code == 404
    r = client.post("/api/cron/sync", headers={"X-Cron-Token": "test-cron-token"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"synced", "results", "seconds"}


def test_hosted_database_requires_secret_key():
    from app.config import Settings

    s = Settings(database_url="postgresql://u:p@db.example.com/x", secret_key="")
    with pytest.raises(RuntimeError):
        s.signing_key()
    assert s.sqlalchemy_url().startswith("postgresql+psycopg://")
    assert Settings(database_url="postgres://u:p@h/x").sqlalchemy_url() == "postgresql+psycopg://u:p@h/x"
