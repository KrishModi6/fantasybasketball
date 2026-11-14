"""NBA game predictions for the Overview page: fetched, checked, kept, and never a reason for the page to fail."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.orm import User
from app.security import create_token
from app.services import games

URL = "https://example.test/predictions.json"


def _feed(**over) -> dict:
    game = {
        "id": "401909296", "tip": "2026-11-14T00:00Z", "status": "final", "neutral": False, "home": "IND", "away": "NY",
        "home_id": 11, "away_id": 18, "home_win": 0.3287, "home_score": 111, "away_score": 116, "home_range": [95, 126],
        "away_range": [101, 132], "home_pts": 104, "away_pts": 117,
        "factors": [{"factor": "Team strength (Elo)", "points": -14.5}, {"factor": "Home court", "points": 6.9}],
        "home_out": ["Tyrese Haliburton"], "away_out": [], "home_questionable": ["Aaron Nesmith"], "away_questionable": [],
    }
    feed = {"format": 1, "generated_at": "2026-11-14T03:24:42+00:00", "data_through": "2026-11-12", "injuries": True,
            "backtest": {"games": 7393, "accuracy": 0.6704, "elo_accuracy": 0.6522, "home_accuracy": 0.5511},
            "days": [{"date": "2026-11-13", "games": [game]}, {"date": "2026-11-14", "games": []}]}
    return {**feed, **over}


class Web:
    """Stands in for httpx.get: answers with a body (or raises), and counts the requests."""

    def __init__(self, body=None, error: Exception | None = None, status: int = 200):
        self.body, self.error, self.status, self.calls = body, error, status, 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        content = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return httpx.Response(self.status, content=content, request=httpx.Request("GET", url))


@pytest.fixture
def feed(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "games_url", URL)
    monkeypatch.setattr(games, "CACHE_DIR", tmp_path)
    games.reset()
    yield lambda web: monkeypatch.setattr(games.httpx, "get", web)
    games.reset()


def test_the_panel_is_off_without_an_address(monkeypatch):
    monkeypatch.setattr(get_settings(), "games_url", "")
    monkeypatch.setattr(games.httpx, "get", lambda *a, **k: pytest.fail("nothing should be fetched"))
    games.reset()
    assert games.predictions() == {"available": False}


def test_the_published_games_come_through_with_courtsides_team_names(feed):
    web = Web(_feed())
    feed(web)
    out = games.predictions()
    assert out["available"] and out["generated_at"] == "2026-11-14T03:24:42+00:00" and out["injuries"] is True
    assert out["backtest"] == {"games": 7393.0, "accuracy": 0.6704, "elo_accuracy": 0.6522, "home_accuracy": 0.5511}
    assert [(d["date"], len(d["games"])) for d in out["days"]] == [("2026-11-13", 1), ("2026-11-14", 0)]
    g = out["days"][0]["games"][0]
    assert (g["home"], g["away"]) == ("IND", "NYK")  # ESPN's scoreboard says NY; the rest of Courtside says NYK
    assert g["home_win"] == 0.3287 and (g["home_score"], g["away_score"]) == (111.0, 116.0)
    assert (g["status"], g["home_pts"], g["away_pts"]) == ("final", 104.0, 117.0)
    assert g["factors"][0] == {"factor": "Team strength (Elo)", "points": -14.5}
    assert g["home_out"] == ["Tyrese Haliburton"] and g["home_questionable"] == ["Aaron Nesmith"]
    assert games.predictions() == out and web.calls == 1  # kept for a few minutes: one request


def test_only_what_the_page_shows_gets_through(feed):
    bad = _feed()
    bad["days"][0]["games"] += [
        {"home_id": 2, "away_id": 5, "home_win": 1.7},  # not a probability
        {"home_id": 2, "away_id": 5, "home_win": "0.6"},  # not a number
        "not a game",
        {"home_id": 99, "away_id": 5, "home": "<b>BOS</b>toolong", "away": "CLE", "home_win": 0.6, "status": "exploded",
         "factors": [{"factor": "x" * 500, "points": 3}, {"factor": 7, "points": 1}, "nope"], "home_out": ["A" * 300, 5],
         "home_range": [1, "two"], "extra": {"anything": True}},
    ]
    bad["extra"] = "ignored"
    feed(Web(bad))
    out = games.predictions()
    assert "extra" not in out and len(out["days"][0]["games"]) == 2
    odd = out["days"][0]["games"][1]
    assert odd["home"] == "<b>BOS" and odd["away"] == "CLE" and odd["status"] == "scheduled"  # cut short; React escapes it
    assert odd["factors"] == [{"factor": "x" * 80, "points": 3.0}] and odd["home_out"] == ["A" * 80]
    assert odd["home_range"] is None and "extra" not in odd


@pytest.mark.parametrize("body", [{"format": 2, "days": []}, {"days": []}, [], b"not json", b"x" * (games.MAX_BYTES + 1)],
                         ids=["a newer format", "no format", "not an object", "not json", "too large"])
def test_a_file_it_cant_read_shows_nothing(feed, body):
    feed(Web(body))
    assert games.predictions() == {"available": False}


def test_the_last_good_copy_is_used_when_the_file_cant_be_fetched(feed, monkeypatch):
    feed(Web(_feed()))
    first = games.predictions()
    assert first["available"]
    # Later, in a new run of the app, GitHub can't be reached: the copy saved on disk still shows.
    games.reset()
    down = Web(error=httpx.ConnectError("offline"))
    feed(down)
    again = games.predictions()
    assert again == first and down.calls == 1
    assert games.predictions() == first and down.calls == 1  # and it doesn't ask again straight away
    # With no copy at all, there's simply nothing to show.
    games.reset()
    (games.CACHE_DIR / "games.json").unlink()
    feed(Web(status=404, body={}))
    assert games.predictions() == {"available": False}


def test_the_endpoint_needs_a_sign_in_and_never_fails(feed):
    feed(Web(_feed()))
    with TestClient(app) as client:
        assert client.get("/api/games").status_code == 401
        with SessionLocal() as db:
            user = User(email=f"{uuid.uuid4().hex}@example.com", display_name="Someone", password_hash="!")
            db.add(user)
            db.commit()
            auth = {"Authorization": f"Bearer {create_token(user.id)}"}
        r = client.get("/api/games", headers=auth)
        assert r.status_code == 200 and r.json()["days"][0]["games"][0]["away"] == "NYK"
        games.reset()
        (games.CACHE_DIR / "games.json").unlink()
        feed(Web(error=httpx.ReadTimeout("slow")))
        r = client.get("/api/games", headers=auth)
        assert r.status_code == 200 and r.json() == {"available": False}
