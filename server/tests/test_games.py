"""NBA game predictions for the Overview page: fetched, checked, kept, and never a reason for the page to fail."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

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


# ---- Live scores while a game is on --------------------------------------------------------------------

BOARD = "https://example.test/scoreboard"
NOW = datetime(2026, 11, 16, 2, 30, tzinfo=timezone.utc)  # 9:30 PM Eastern on the 15th


def _event(gid="401909993", state="in", home="55", away="68", period=3, clock=621.0, display="10:21"):
    return {"id": gid, "status": {"clock": clock, "displayClock": display, "period": period, "type": {"state": state}},
            "competitions": [{"competitors": [{"homeAway": "home", "score": home, "team": {"id": "23"}},
                                              {"homeAway": "away", "score": away, "team": {"id": "25"}}]}]}


def _tonight(*games_: dict) -> dict:
    """A published file with tonight's games, as the 8 PM run left them: not started yet."""
    base = {"id": "401909993", "tip": "2026-11-16T02:00Z", "status": "scheduled", "home": "POR", "away": "OKC",
            "home_id": 22, "away_id": 25, "home_win": 0.25, "home_score": 108, "away_score": 116,
            "home_pts": None, "away_pts": None, "factors": [], "home_out": [], "away_out": []}
    return _feed(days=[{"date": "2026-11-15", "games": [{**base, **g} for g in (games_ or ({},))]},
                       {"date": "2026-11-16", "games": [{**base, "id": "9", "tip": "2026-11-17T00:00Z"}]}])


class Sites:
    """Stands in for httpx.get with one Web for the published file and one for ESPN's scoreboard."""

    def __init__(self, published: Web, board: Web):
        self.published, self.board, self.asked = published, board, []

    def __call__(self, url, **kwargs):
        if url == BOARD:
            self.asked.append(kwargs.get("params", {}).get("dates"))
            return self.board(url, **kwargs)
        return self.published(url, **kwargs)


@pytest.fixture
def live(feed, monkeypatch):
    monkeypatch.setattr(get_settings(), "games_scores_url", BOARD)

    def serve(published: dict, board) -> Sites:
        sites = Sites(Web(published), board if isinstance(board, Web) else Web({"events": board}))
        feed(sites)
        return sites
    return serve


def _first(out: dict) -> dict:
    return out["days"][0]["games"][0]


def test_a_game_that_is_on_gets_espns_score_clock_and_a_live_chance(live):
    sites = live(_tonight(), [_event()])
    g = _first(games.predictions(now=NOW))
    assert (g["status"], g["home_pts"], g["away_pts"], g["period"], g["clock"]) == ("live", 55.0, 68.0, 3, "Q3 10:21")
    assert g["home_win"] == 0.25  # the pregame odds stay as they were published
    assert 0 < g["live_home_win"] < 0.1  # down 13 with a quarter and a half to go, and the underdog
    assert sites.asked == ["20261115"]  # only the day with a game on; tomorrow's hasn't started
    tomorrow = games.predictions(now=NOW)["days"][1]["games"][0]
    assert tomorrow["status"] == "scheduled" and tomorrow["live_home_win"] is None


def test_live_scores_are_kept_for_a_few_seconds_then_read_again(live):
    sites = live(_tonight(), [_event()])
    games.predictions(now=NOW)
    games.predictions(now=NOW)
    assert len(sites.asked) == 1 and sites.published.calls == 1
    sites.board.body = {"events": [_event(home="60", away="70", clock=300.0, display="5:00")]}
    games._boards["2026-11-15"]["at"] = 0  # LIVE_TTL_S later
    g = _first(games.predictions(now=NOW))
    assert len(sites.asked) == 2 and sites.published.calls == 1  # the published file isn't fetched again
    assert (g["home_pts"], g["away_pts"], g["clock"]) == (60.0, 70.0, "Q3 5:00")


def test_nothing_is_asked_of_espn_before_tip_off_long_after_it_or_when_the_scores_are_off(live, monkeypatch):
    sites = live(_tonight(), [_event()])
    g = _first(games.predictions(now=datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)))  # an hour before tip
    assert sites.asked == [] and g["status"] == "scheduled" and g["live_home_win"] is None
    games.predictions(now=datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc))  # seven hours after: over, or postponed
    assert sites.asked == []
    monkeypatch.setattr(get_settings(), "games_scores_url", "")
    games.reset()
    assert _first(games.predictions(now=NOW))["status"] == "scheduled" and sites.asked == []


def test_a_game_espn_has_as_final_is_final_and_its_day_rests(live):
    sites = live(_tonight({"status": "live", "home_pts": 40, "away_pts": 44}), [_event(state="post", home="101", away="118")])
    g = _first(games.predictions(now=NOW))
    assert (g["status"], g["home_pts"], g["away_pts"], g["clock"], g["live_home_win"]) == ("final", 101.0, 118.0, None, None)
    assert games._boards["2026-11-15"]["at"] - time.monotonic() > games.LIVE_TTL_S  # all over: read far less often
    assert len(sites.asked) == 1


def test_a_game_the_file_has_as_final_is_left_alone(live):
    sites = live(_tonight({"status": "final", "home_pts": 99, "away_pts": 98}), [_event()])
    g = _first(games.predictions(now=NOW))
    assert (g["status"], g["home_pts"]) == ("final", 99.0) and sites.asked == []


def test_when_espn_cant_be_reached_the_last_scores_stay(live):
    sites = live(_tonight(), [_event()])
    assert _first(games.predictions(now=NOW))["home_pts"] == 55.0
    sites.board.error = httpx.ConnectError("offline")
    games._boards["2026-11-15"]["at"] = 0
    g = _first(games.predictions(now=NOW))
    assert g["home_pts"] == 55.0 and len(sites.asked) == 2
    games.predictions(now=NOW)
    assert len(sites.asked) == 2  # and it waits a minute before asking again


@pytest.mark.parametrize("board", [Web(error=httpx.ReadTimeout("slow")), Web(b"not json"), Web({"no": "events"}),
                                   Web(status=503, body={})], ids=["slow", "not json", "no events", "down"])
def test_without_a_scoreboard_the_published_game_still_shows(live, board):
    live(_tonight(), board)
    g = _first(games.predictions(now=NOW))
    assert g["status"] == "scheduled" and g["home_win"] == 0.25 and g["home_pts"] is None


def test_a_scoreboard_it_cant_read_changes_nothing(live):
    junk = [
        "not a game", {"id": True}, {"id": "401909993"},
        _event(home="55<b>", away="68"),  # not a score
        _event(state="halftime"),  # not a state ESPN uses
        {**_event(), "competitions": [{"competitors": [{"homeAway": "home", "score": "1"}]}]},  # one side
    ]
    postponed = _event(state="post", home="0", away="0")
    postponed["status"]["type"]["name"] = "STATUS_POSTPONED"
    junk.append(postponed)  # later in the list, so it's the one ESPN's id points to
    live(_tonight(), junk)
    g = _first(games.predictions(now=NOW))
    assert g["status"] == "scheduled" and g["home_pts"] is None and g["clock"] is None


@pytest.mark.parametrize("period,clock,display,shown,left", [
    (1, 720.0, "12:00", "Q1 12:00", 2880), (2, 0.0, "0.0", "Halftime", 1440), (3, 0.0, "0.0", "End of Q3", 720),
    (4, 34.2, "34.2", "Q4 34.2", 34.2), (5, 120.0, "2:00", "OT 2:00", 120), (6, 0.0, "0.0", "End of 2OT", 0),
    (4, 9999.0, "", "Q4 12:00", 720),  # a clock longer than a quarter is a quarter
])
def test_the_period_and_clock_read_like_a_scoreboard(period, clock, display, shown, left):
    _gid, g = games._live_game(_event(period=period, clock=clock, display=display))
    assert (g["period"], g["clock"], g["seconds_left"]) == (period, shown, left)


def test_the_live_chance_starts_at_the_pregame_odds_and_settles_as_time_runs_out():
    assert games.live_chance(0.25, 0, 0, 2880) == pytest.approx(0.25, abs=1e-4)
    assert games.live_chance(0.5, 0, 0, 1440) == pytest.approx(0.5)
    assert games.live_chance(0.5, 10, 0, 60) > 0.99 and games.live_chance(0.5, 0, 10, 60) < 0.01
    assert games.live_chance(0.9, 0, 0, 0) == 0.5 and games.live_chance(0.1, 1, 0, 0) == 1.0  # the buzzer decides
    # A lead counts for more as the clock runs down, and the favourite needs less of one.
    assert games.live_chance(0.5, 5, 0, 2000) < games.live_chance(0.5, 5, 0, 300)
    assert games.live_chance(0.3, 3, 0, 1000) < games.live_chance(0.7, 3, 0, 1000)
    leads = [games.live_chance(0.4, lead, 0, 900) for lead in range(-20, 21, 4)]
    assert leads == sorted(leads)


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
