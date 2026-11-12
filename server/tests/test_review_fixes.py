"""Regression tests for the bugs the 0.5.10 code review found: the season simulation, the matchup simulator,
refreshes that got stuck, the trade evaluator's shared caches, and the desktop engine's local-only guard."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

import fbb.strength as S
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.orm import League, LeagueMember, Snapshot, User
from app.security import create_token, encrypt_json
from app.services import sync
from app.services.analysis import pack_snapshot
from factories import make_snapshot
from fbb.connectors import espn
from fbb.connectors.base import ConnectorError
from fbb.connectors.fileimport import parse_upload
from fbb.context import AnalysisContext
from fbb.matchup_sim import MatchupSimulator
from fbb.models import Matchup, ScoringSettings
from fbb.projections import INJURY_DEFAULT_GAMES, ProjectionEngine
from fbb.schedule import build_matchup_periods
from fbb.strength import MatchupModel, league_bracket, league_strength, simulate_season, team_windows
from fbb.trades import TradeEvaluator


def _ctx(snap) -> AnalysisContext:
    return AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())


def _start_week(snap, mp: int) -> None:
    snap.current_matchup_period = mp
    snap.current_period = snap.settings.matchup_periods[mp][0]


# ---- season simulation: points already scored -------------------------------------------------------


def test_the_season_simulation_keeps_points_already_scored():
    snap = make_snapshot()
    _start_week(snap, 2)
    m = next(m for m in snap.matchups if m.matchup_period == 1)
    m.home_score, m.away_score = 1400.0, 1300.0  # week 1 is over; ESPN hasn't posted the result
    ctx = _ctx(snap)
    mm = MatchupModel(ctx, team_windows(ctx))
    rng = np.random.default_rng(0)
    level = 1 + S.SEASON_TEAM_CV * rng.standard_normal((5000, 2))
    margin = S._margin(mm, m.home_team_id, m.away_team_id, 1, level[:, 0], level[:, 1], rng)
    # It used to scale the final scores by each team's simulated level, and the 100-point winner lost 30% of seasons.
    assert (margin > 0).all()
    assert margin.mean() == pytest.approx(100.0, abs=1.0)


def test_a_week_in_progress_only_moves_by_the_points_still_to_come():
    snap = make_snapshot()
    m = next(m for m in snap.matchups if m.matchup_period == 1)
    ctx = _ctx(snap)
    base = MatchupModel(ctx, team_windows(ctx))
    left_home = base.parts(m.home_team_id, 1)[0]
    m.home_score, m.away_score = 5000.0, 5000.0  # far more on the board than is left to play
    mm = MatchupModel(ctx, team_windows(ctx))
    assert mm.parts(m.home_team_id, 1) == pytest.approx((left_home, 5000.0, base.parts(m.home_team_id, 1)[2]))
    rng = np.random.default_rng(1)
    level = 1 + S.SEASON_TEAM_CV * rng.standard_normal((5000, 2))
    spread = S._margin(mm, m.home_team_id, m.away_team_id, 1, level[:, 0], level[:, 1], rng).std()
    same = S._margin(base, m.home_team_id, m.away_team_id, 1, level[:, 0], level[:, 1], rng).std()
    assert spread == pytest.approx(same, rel=0.05)  # the 5,000 already scored add nothing to the spread


def test_a_finished_last_week_gives_its_winner_the_win_in_every_simulated_season():
    snap = make_snapshot()
    team = {t.id: t for t in snap.teams}
    for m in snap.matchups:
        if m.matchup_period < 12:
            m.winner = "HOME"
            team[m.home_team_id].wins += 1
            team[m.away_team_id].losses += 1
        else:
            m.home_score, m.away_score = 1300.0, 1400.0  # the away team won week 12, not posted yet
    _start_week(snap, 13)
    last = [m for m in snap.matchups if m.matchup_period == 12]
    st = {ts.team_id: ts for ts in league_strength(_ctx(snap))}
    for m in last:
        assert st[m.away_team_id].proj_wins == pytest.approx(team[m.away_team_id].wins + 1)
        assert st[m.home_team_id].proj_wins == pytest.approx(team[m.home_team_id].wins)


# ---- season simulation: the playoffs as they stand ---------------------------------------------------


def _playoff_league(n_teams: int, playoff_teams: int, rounds: list[int], records: dict[int, tuple[int, int]]):
    snap = make_snapshot(n_teams=n_teams)
    snap.settings.playoff_teams = playoff_teams
    snap.settings.playoff_round_weeks = rounds
    snap.settings.matchup_periods = build_matchup_periods(snap.schedule, 12, rounds)
    for t in snap.teams:
        t.wins, t.losses = records[t.id]
        t.points_for = 1000.0 * t.wins
    for m in snap.matchups:
        m.winner = "HOME"
    return snap


def _bracket_game(mp: int, home: int, away: int, winner: str = "UNDECIDED", tier: str = "WINNERS_BRACKET") -> Matchup:
    scores = {"HOME": (900.0, 700.0), "AWAY": (700.0, 900.0), "UNDECIDED": (0.0, 0.0)}[winner]
    return Matchup(matchup_period=mp, home_team_id=home, away_team_id=away, home_score=scores[0], away_score=scores[1],
                   winner=winner, is_playoff=True, playoff_tier=tier)


def test_title_odds_follow_the_real_bracket_in_the_championship_week():
    records = {1: (9, 3), 2: (8, 4), 3: (7, 5), 4: (6, 6), 5: (4, 8), 6: (2, 10)}
    snap = _playoff_league(6, 4, [1, 1], records)
    snap.matchups += [
        _bracket_game(13, 1, 4, "AWAY"), _bracket_game(13, 2, 3, "HOME"),  # the semifinals: 4 upset 1, 2 beat 3
        _bracket_game(13, 5, 6, "HOME", tier="LOSERS_CONSOLATION_LADDER"),
        _bracket_game(14, 4, 2),  # the final
        _bracket_game(14, 1, 3, tier="WINNERS_CONSOLATION_LADDER"),  # third place: not a title game
    ]
    _start_week(snap, 14)
    ctx = _ctx(snap)
    out = simulate_season(ctx, MatchupModel(ctx, team_windows(ctx)))
    # It used to seed and replay the whole bracket, so teams already knocked out kept title odds.
    assert [out[t]["title_odds"] for t in (1, 3, 5, 6)] == [0.0, 0.0, 0.0, 0.0]
    assert out[2]["title_odds"] + out[4]["title_odds"] == pytest.approx(1.0)
    assert 0.02 < out[4]["title_odds"] < 0.98
    assert [out[t]["playoff_odds"] for t in range(1, 7)] == [1.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    assert [out[t]["avg_seed"] for t in (1, 2, 3, 4)] == [1.0, 2.0, 3.0, 4.0]


def test_a_decided_first_round_game_stands_while_the_other_is_played():
    records = {1: (9, 3), 2: (8, 4), 3: (7, 5), 4: (6, 6)}
    snap = _playoff_league(4, 4, [1, 1], records)
    snap.matchups += [_bracket_game(13, 1, 4, "AWAY"), _bracket_game(13, 2, 3)]
    _start_week(snap, 13)
    ctx = _ctx(snap)
    out = simulate_season(ctx, MatchupModel(ctx, team_windows(ctx)))
    assert out[1]["title_odds"] == 0.0  # lost its semifinal
    assert all(out[t]["title_odds"] > 0 for t in (2, 3, 4))
    assert sum(out[t]["title_odds"] for t in (2, 3, 4)) == pytest.approx(1.0)


def test_first_round_byes_are_the_best_records_without_a_game():
    records = {1: (11, 1), 2: (10, 2), 3: (8, 4), 4: (7, 5), 5: (6, 6), 6: (5, 7), 7: (2, 10), 8: (1, 11)}
    snap = _playoff_league(8, 6, [1, 1, 1], records)
    snap.matchups += [_bracket_game(13, 3, 6), _bracket_game(13, 4, 5)]  # seeds 1 and 2 sit this round out
    _start_week(snap, 13)
    ctx = _ctx(snap)
    col = {t.id: i for i, t in enumerate(snap.teams)}
    playoff_mps = sorted(mp for mp in snap.settings.matchup_periods if mp > 12)
    live = league_bracket(ctx, col, playoff_mps)
    ids = [t.id for t in snap.teams]
    slots = [ids[c] if c >= 0 else None for c in live.slots]
    # Seed 1 waits for the winner of 4 v 5, seed 2 for the winner of 3 v 6.
    assert [set(slots[i:i + 2]) for i in range(0, 8, 2)] == [{1, None}, {4, 5}, {2, None}, {3, 6}]
    out = simulate_season(ctx, MatchupModel(ctx, team_windows(ctx)))
    assert out[7]["title_odds"] == out[8]["title_odds"] == 0.0
    assert all(out[t]["title_odds"] > 0 for t in range(1, 7))
    assert [out[t]["playoff_odds"] for t in (1, 2, 7, 8)] == [1.0, 1.0, 0.0, 0.0]


def test_without_bracket_games_the_bracket_is_seeded_from_the_standings():
    records = {1: (9, 3), 2: (8, 4), 3: (7, 5), 4: (6, 6)}
    snap = _playoff_league(4, 4, [1, 1], records)
    ctx = _ctx(snap)
    col = {t.id: i for i, t in enumerate(snap.teams)}
    assert league_bracket(ctx, col, [13, 14]) is None  # the regular season: nothing listed
    snap.matchups += [_bracket_game(13, 1, 4, tier=None), _bracket_game(13, 2, 3, tier=None)]
    assert league_bracket(_ctx(snap), col, [13, 14]) is None  # a source that doesn't mark bracket games
    # A listing this can't read (three teams left in a four-team bracket's final) is left to the standings too.
    snap.matchups += [_bracket_game(14, 1, 2), _bracket_game(14, 3, 4)]
    for m in snap.matchups:
        if m.matchup_period > 12:
            m.playoff_tier = "WINNERS_BRACKET"
    assert league_bracket(_ctx(snap), col, [13, 14]) is None


def test_espn_bracket_games_are_marked():
    data = {"schedule": [
        {"matchupPeriodId": 20, "playoffTierType": "WINNERS_BRACKET", "home": {"teamId": 1}, "away": {"teamId": 4}},
        {"matchupPeriodId": 20, "playoffTierType": "LOSERS_CONSOLATION_LADDER", "home": {"teamId": 5}, "away": {"teamId": 6}},
        {"matchupPeriodId": 3, "playoffTierType": "NONE", "home": {"teamId": 1}, "away": {"teamId": 2}},
        {"matchupPeriodId": 4, "home": {"teamId": 1}, "away": {"teamId": 2}},
    ]}
    assert [m.playoff_tier for m in espn.parse_matchups(data, 19)] == ["WINNERS_BRACKET", "LOSERS_CONSOLATION_LADDER",
                                                                       "NONE", None]


# ---- matchup simulator: double-doubles ---------------------------------------------------------------


def test_double_doubles_add_up_over_a_week_in_a_category_league():
    snap = make_snapshot()
    snap.settings.scoring = ScoringSettings(kind="categories", categories=["PTS", "REB", "DD"], points={})
    ctx = _ctx(snap)
    r = MatchupSimulator(ctx, seed=4).simulate(1, 2, 2, n=6000)
    row = next(x for x in r["categories"]["rows"] if x["category"] == "DD")
    days = ctx.matchup_days(2)
    expected = [ctx.project_window(ctx.team_roster(t), days, track_stats=True, team_id=t).stats_by_mp[2]["DD"] for t in (1, 2)]
    # A week's total used to be capped at one, so the category tied in 96% of weeks.
    assert expected[0] > 2 and expected[1] > 2
    assert row["a_mean"] == pytest.approx(expected[0], rel=0.1)
    assert row["b_mean"] == pytest.approx(expected[1], rel=0.1)
    assert row["tie"] < 0.5


def test_a_player_gets_at_most_one_double_double_a_game():
    snap = make_snapshot()
    ctx = _ctx(snap)
    pid = ctx.team_roster(1)[0]
    ctx.proj[pid].per_game["DD"] = 0.9
    sim = MatchupSimulator(ctx, seed=2)
    days = ctx.matchup_days(2)
    players, plans = sim._plan([pid], days, set())
    totals = sim._simulate_team(players, plans, 4000)[0]
    dd = totals[:, 9 + 2]  # EVENTS: ..., "TO", "DD", "TD", "PF"
    games = sum(1 for d in days if ctx.p_play(pid).get(d, 0) > 0)
    assert dd.max() <= games
    assert dd.mean() == pytest.approx(0.9 * sum(ctx.p_play(pid).get(d, 0) for d in days), rel=0.08)


# ---- projections: a return date that has passed -------------------------------------------------------


def test_a_past_return_date_doesnt_clear_an_out_player():
    snap = make_snapshot()
    snap.current_period = 20
    today = snap.schedule.period_dates[20]
    p = snap.players["1"]
    p.injury_status = "OUT"

    def games_out(return_date) -> float:
        p.expected_return = return_date
        return ProjectionEngine(snap, model=None).project_all()["1"].games_out

    assert games_out(today - timedelta(days=3)) == INJURY_DEFAULT_GAMES["OUT"]  # it used to be 0: back already
    assert games_out(today) == 0.0  # expected back today
    week_away = games_out(today + timedelta(days=7))
    assert week_away == snap.schedule.team_games_between(p.pro_team, 20, 26)
    p.injury_note = "Expected to miss 6-8 weeks"
    assert games_out(today - timedelta(days=3)) > 20  # with a stale date, the note still counts


# ---- trade evaluator: one cache, many threads ---------------------------------------------------------


def test_the_trade_evaluators_window_cache_survives_concurrent_searches(monkeypatch):
    snap = make_snapshot()
    ctx = _ctx(snap)
    ev = TradeEvaluator(ctx)
    monkeypatch.setattr(TradeEvaluator, "WINDOW_CACHE", 3)  # constant eviction: the old cache raised KeyError
    rosters = [ctx.team_roster(t)[:k] for t in (1, 2, 3, 4) for k in (5, 6, 7)]
    errors: list[BaseException] = []

    def search(offset: int) -> None:
        try:
            for i in range(60):
                roster = rosters[(i + offset) % len(rosters)]
                assert ev._window(1, roster).mean == pytest.approx(ctx.project_window(roster, team_id=1).mean)
        except BaseException as exc:  # noqa: BLE001 - reported below, in the test's own thread
            errors.append(exc)

    threads = [threading.Thread(target=search, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ev._windows) <= 3


# ---- refreshes -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as c:
        yield c


def _user(name: str = "Someone") -> tuple[str, dict]:
    with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4().hex}@example.com", display_name=name, password_hash="!")
        db.add(user)
        db.commit()
        return user.id, {"Authorization": f"Bearer {create_token(user.id)}"}


def _league(owner_id: str, source: str = "demo") -> str:
    with SessionLocal() as db:
        league = League(name="Test", season=2027, source=source, owner_id=owner_id, last_sync_status="ok",
                        connector_config=encrypt_json({"league_id": "1"}))
        db.add(league)
        db.flush()
        db.add(LeagueMember(league_id=league.id, user_id=owner_id, role="owner"))
        db.add(Snapshot(league_id=league.id, source=source, data=pack_snapshot(make_snapshot())))
        db.commit()
        return league.id


class _Connector:
    def fetch_snapshot(self):
        snap = make_snapshot()
        snap.source = "demo"
        return snap


def _status(league_id: str) -> tuple[str, str | None]:
    with SessionLocal() as db:
        league = db.get(League, league_id)
        return league.last_sync_status, league.last_sync_error


def test_a_refresh_that_fails_after_the_fetch_isnt_left_running(client, monkeypatch):
    user_id, _ = _user()
    league_id = _league(user_id)
    monkeypatch.setattr(sync, "build_connector", lambda league: _Connector())
    monkeypatch.setattr(sync, "fetch_missing_bios", lambda *a, **k: 0)
    monkeypatch.setattr(sync, "attach_tracking", lambda *a, **k: 0)

    def broken(*args, **kwargs):
        raise KeyError("a player ESPN sent in a shape nobody expected")

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_record_projection_history", broken)
        sync.run_sync(league_id)
    # It used to stay "running", which every later refresh skips: old data until the app restarted.
    status, error = _status(league_id)
    assert status == "error" and "Unexpected error" in error
    assert league_id in sync.due_leagues()  # never synced, and no longer skipped as running
    sync.run_sync(league_id)
    assert _status(league_id) == ("ok", None)


def test_a_bad_injury_report_or_news_feed_doesnt_stop_an_espn_refresh(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def league(self, *args, **kwargs):
            return {"settings": {"name": "L"}, "teams": [], "schedule": [], "status": {}}

        def league_players(self, *args, **kwargs):
            return []

        def pro_schedule(self, season):
            return {"settings": {"proTeams": []}}

        def injuries(self):  # a return date that isn't a date
            return {"injuries": [{"injuries": [{"athlete": {"links": [{"href": "/id/7"}]},
                                                "details": {"returnDate": "next week"}}]}]}

        def news(self):  # ESPN sends a list where a headline's links used to be an object
            return {"articles": [{"id": 1, "headline": "x", "links": ["oops"]}]}

        def close(self):
            pass

    monkeypatch.setattr(espn, "EspnClient", Client)
    snap = espn.EspnConnector("1", 2027).fetch_snapshot()
    assert snap.source == "espn" and snap.news == []


# ---- the desktop engine answers only this computer ----------------------------------------------------


def test_the_local_engine_refuses_requests_addressed_to_another_host(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "local_mode", True)
    assert client.post("/api/auth/local").status_code == 200
    assert client.post("/api/auth/local", headers={"Host": "localhost:52714"}).status_code == 200
    # A web page that points its own domain at 127.0.0.1 (DNS rebinding) could read the sign-in token.
    r = client.post("/api/auth/local", headers={"Host": "rebind.example.com:52714"})
    assert r.status_code == 403 and "token" not in r.text
    assert client.get("/api/health", headers={"Host": "rebind.example.com"}).status_code == 403


def test_a_shared_server_answers_under_its_own_name(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "local_mode", False)
    assert client.get("/api/health", headers={"Host": "courtside.example.com"}).status_code == 200


# ---- smaller fixes ----------------------------------------------------------------------------------


def test_the_league_creator_cant_be_demoted(client):
    user_id, auth = _user()
    league_id = _league(user_id)
    r = client.patch(f"/api/leagues/{league_id}/members/{user_id}", json={"role": "member"}, headers=auth)
    assert r.status_code == 400
    assert client.patch(f"/api/leagues/{league_id}/members/{user_id}", json={"role": "owner"}, headers=auth).status_code == 200


def test_trade_suggestions_refuse_a_partner_outside_the_league(client):
    user_id, auth = _user()
    league_id = _league(user_id)
    for partner in (99, 1):  # no such team; the team itself
        r = client.get(f"/api/leagues/{league_id}/trades/suggestions", params={"team_id": 1, "partner_id": partner},
                       headers=auth)
        assert r.status_code == 422


def test_a_league_file_keeps_its_schedule_settings_and_says_what_is_wrong():
    spec = parse_upload("league.json", json.dumps({
        "teams": [{"name": "A", "players": ["Nikola Jokic"]}, {"name": "B", "players": [{"name": "Luka Doncic"}]}],
        "regular_season_matchups": 18, "playoff_teams": 6, "playoff_round_weeks": [1, 1, 2],
        "records": {"A": {"wins": 3, "losses": 1}},
    }).encode())
    assert spec["regular_season_matchups"] == 18 and spec["playoff_teams"] == 6
    assert spec["playoff_round_weeks"] == [1, 1, 2] and spec["records"]["A"]["wins"] == 3
    assert [p["name"] for t in spec["teams"] for p in t["players"]] == ["Nikola Jokic", "Luka Doncic"]
    for teams in (["Team A"], [{"name": "A", "players": "Jokic"}], [{"name": "A", "players": [{}]}]):
        with pytest.raises(ConnectorError):
            parse_upload("league.json", json.dumps({"teams": teams}).encode())
