"""End-to-end API test against a demo league (needs network access to ESPN on first run; cached after)."""

from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["FBB_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
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

    r = client.get(f"/api/leagues/{lid}/rankings", headers=owner)
    assert r.status_code == 200, r.text
    teams = r.json()["teams"]
    assert [t["rank"] for t in teams] == list(range(1, 11))
    assert abs(sum(t["title_odds"] for t in teams) - 1) < 0.02
    assert teams[0]["explanation"]["summary"]

    tid = teams[0]["team_id"]
    assert client.get(f"/api/leagues/{lid}/teams/{tid}", headers=owner).status_code == 200
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


def test_auth_errors(client):
    assert client.get("/api/leagues").status_code == 401
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert r.status_code == 401
