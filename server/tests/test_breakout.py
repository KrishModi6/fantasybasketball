"""The breakout model: what counts as a breakout, what it may know, and what the app shows."""

from __future__ import annotations

import json
import math
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.orm import League, LeagueMember, Snapshot, User
from app.security import create_token, encrypt_json
from app.services.analysis import pack_snapshot
from factories import make_line, make_snapshot
from fbb import breakout as B
from fbb.baseline import league_rates
from fbb.history import HistoryDataset, load_history
from fbb.models import ExpertProjection, SeasonLine
from fbb.projections import ProjectionEngine


def _line(season: int, gp: float, mpg: float, scale: float) -> SeasonLine:
    return make_line(season, gp, mpg, scale)


def test_a_breakout_is_a_big_rise_to_a_useful_level_over_enough_games():
    last = _line(2025, 60, 18, 1.0)
    assert 12 < B.fp_per_game(last) < 20
    assert B.is_breakout(last, _line(2026, 70, 32, 1.1)) is True
    assert B.is_breakout(last, _line(2026, 70, 21, 1.0)) is False  # a point or two more
    assert B.is_breakout(last, _line(2026, 20, 32, 1.1)) is False  # not enough games
    assert B.is_breakout(last, None) is False  # didn't play
    small = _line(2025, 60, 8, 1.0)
    assert B.fp_per_game(_line(2026, 70, 16, 1.2)) - B.fp_per_game(small) >= B.MIN_GAIN
    assert B.is_breakout(small, _line(2026, 70, 16, 1.2)) is False  # a big rise, but still below the level that matters
    assert B.is_breakout(_line(2025, 10, 20, 1.0), _line(2026, 70, 32, 1.1)) is None  # no level to break out from
    assert B.is_breakout(None, _line(2026, 70, 32, 1.1)) is None


def test_features_read_only_earlier_seasons():
    hist = [_line(2024, 70, 18, 0.9), _line(2025, 72, 24, 1.0)]
    lg = league_rates([])
    f = B.build_features(hist, 2026, 23.0, 2024, None, lg)
    # The season being predicted, and anything after it, changes nothing.
    later = hist + [_line(2026, 80, 36, 1.6), _line(2027, 80, 36, 1.6)]
    assert B.build_features(later, 2026, 23.0, 2024, None, lg) == pytest.approx(f, nan_ok=True)
    assert set(f) == set(B.FEATURES)
    assert f["years"] == 3 and f["d_mpg"] == pytest.approx(6.0) and f["l1_mpg"] == pytest.approx(24.0)
    assert math.isnan(f["espn_gain"])  # no ESPN projection
    assert f["peak_gap"] == pytest.approx(B.fp_per_game(hist[0]) - B.fp_per_game(hist[1]))  # last season was his best
    assert math.isnan(B.build_features(hist[1:], 2026, 23.0, 2025, None, lg)["peak_gap"])  # no season before it
    assert B.build_features(hist, 2025, 22.0, 2024, None, lg)["l1_mpg"] == pytest.approx(18.0)
    assert B.build_features([_line(2025, 12, 24, 1.0)], 2026, 23.0, 2025, None, lg) is None  # too few games last season


def test_features_measure_usage_efficiency_and_a_down_year():
    line = SeasonLine(season=2025, gp=50, minutes=1500, stats={"PTS": 800, "FGA": 600, "FGM": 280, "FTA": 200, "FTM": 160,
                                                                 "TO": 100, "REB": 300, "AST": 200, "STL": 50, "BLK": 25,
                                                                 "3PM": 80, "3PA": 220})
    best = _line(2023, 70, 34, 1.3)
    f = B.build_features([best, _line(2024, 60, 30, 1.0), line], 2026, 27.0, 2020, None, league_rates([]))
    assert f["l1_usage"] == pytest.approx((600 + 0.44 * 200 + 100) / 1500 * 36)
    assert f["l1_ts"] == pytest.approx(800 / (2 * (600 + 0.44 * 200)))
    assert f["peak_gap"] == pytest.approx(B.fp_per_game(best) - B.fp_per_game(line))
    assert f["peak_mpg_gap"] == pytest.approx(34 - 30)
    espn = ExpertProjection(source="ESPN", season=2026, gp=70, minutes_per_game=34,
                            per_game={"PTS": 20, "REB": 7, "AST": 5, "STL": 1, "BLK": 0.5, "TO": 2.5, "FGM": 7.5, "FGA": 16,
                                      "FTM": 3.5, "FTA": 4.2, "3PM": 1.8})
    with_espn = B.build_features([line], 2026, 27.0, 2020, espn, league_rates([]))
    assert with_espn["espn_mpg_gain"] == pytest.approx(34 - 30)
    assert with_espn["espn_gain"] == pytest.approx(B.SCORING.fantasy_points(espn.per_game) - B.fp_per_game(line))
    assert with_espn["espn_gp_frac"] == pytest.approx(70 / 82)


def test_the_bundled_model_loads_and_its_backtest_beats_the_simple_rankings():
    model = B.BreakoutModel.load()
    assert model is not None and model.features == B.FEATURES
    s = model.metrics["summary"]
    assert s["model"]["auc"] > 0.7
    for baseline in ("youth", "sps", "espn", "minutes"):
        assert s[baseline]["model_same_players"]["auc"] > s[baseline]["auc"] + 0.03
    assert model.metrics["brier"] < model.metrics["brier_base_rate"]
    # Probabilities mean what they say: each calibration band's breakout rate is near its mean prediction.
    for band in model.metrics["calibration"]:
        assert abs(band["predicted"] - band["broke_out"]) < 0.06


def test_probabilities_follow_the_evidence():
    model = B.BreakoutModel.load()
    ds = load_history()
    rows = B.preseason_rows(ds, 2026, league_rates(ds.all_lines()))
    p = model.predict(list(rows.values()))
    assert len(p) > 300 and ((p > 0) & (p < 1)).all()
    assert 0.07 < p.mean() < 0.16  # about one player in nine breaks out
    ages = np.array([f["age"] for f in rows.values()])
    known = ~np.isnan(ages)
    assert p[known & (ages <= 22)].mean() > 2 * p[known & (ages >= 31)].mean()  # the young break out far more often
    why = model.explain(list(rows.values())[:5])
    assert len(why) == 5 and all(r["factor"] in B.GROUPS and r["direction"] in ("up", "down") for rs in why for r in rs)
    assert set(B.FEATURES) == {f for fs in B.GROUPS.values() for f in fs}  # every feature has a reason to show


def _dataset(snap) -> HistoryDataset:
    """The snapshot's own players as the history file would hold them."""
    ds = HistoryDataset(age_season=snap.settings.season)
    for pid, p in snap.players.items():
        ds.lines[pid] = sorted(p.history, key=lambda h: h.season)
        ds.names[pid] = p.name
        if p.age is not None:
            ds.age_now[pid] = p.age
    return ds


def _breaking_out(snap, pid: str = "1") -> float:
    """Ten games into the season, already three points past his breakout line. Returns that line."""
    p = snap.players[pid]
    line = max(B.MIN_LEVEL, B.fp_per_game(next(h for h in p.history if h.season == 2026)) + B.MIN_GAIN)
    p.current = make_line(2027, 10, 34, (line + 3) / B.fp_per_game(make_line(2027, 10, 34, 1.0)))
    return line


def test_candidates_are_ranked_and_show_how_the_season_is_going():
    snap = make_snapshot()
    line = _breaking_out(snap)
    proj = ProjectionEngine(snap, model=None).project_all()
    rows = B.candidates(snap, proj, ds=_dataset(snap))
    assert len(rows) == len(snap.players)  # everyone here played 20+ games last season
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert all(a["probability"] >= b["probability"] for a, b in zip(rows, rows[1:]))
    row = next(r for r in rows if r["id"] == "1")
    assert row["on_pace"] and row["games_played"] == 10
    assert row["breakout_line"] == pytest.approx(line, abs=0.05)
    assert row["season_fp_pg"] >= row["breakout_line"] and row["proj_fp_pg"] == round(proj["1"].fp_pg, 1)
    other = next(r for r in rows if r["id"] == "2")
    assert not other["on_pace"] and other["games_played"] == 0 and other["season_fp_pg"] is None
    json.dumps(rows)  # plain data, ready for the API


def test_a_hot_week_isnt_a_breakout_yet():
    snap = make_snapshot()
    _breaking_out(snap)
    snap.players["1"].current.gp = 5  # the same totals over five games: twice the average, but too few games
    row = next(r for r in B.candidates(snap, ds=_dataset(snap)) if r["id"] == "1")
    assert row["games_played"] == 5 and not row["on_pace"] and row["proj_fp_pg"] is None


def test_the_breakouts_endpoint(monkeypatch):
    snap = make_snapshot()
    _breaking_out(snap)
    monkeypatch.setattr(B, "load_history", lambda directory=None: _dataset(snap))
    with TestClient(app) as client:  # starting the app creates the tables
        with SessionLocal() as db:
            user = User(email=f"{uuid.uuid4().hex}@example.com", display_name="Someone", password_hash="!")
            db.add(user)
            db.flush()
            league = League(name="Test", season=2027, source="demo", owner_id=user.id, last_sync_status="ok",
                            connector_config=encrypt_json({}))
            db.add(league)
            db.flush()
            db.add(LeagueMember(league_id=league.id, user_id=user.id, role="owner"))
            db.add(Snapshot(league_id=league.id, source="demo", data=pack_snapshot(snap)))
            db.commit()
            league_id, auth = league.id, {"Authorization": f"Bearer {create_token(user.id)}"}
        assert client.get(f"/api/leagues/{league_id}/breakouts").status_code == 401
        r = client.get(f"/api/leagues/{league_id}/breakouts", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["season"] == 2027 and body["total"] == len(snap.players) and body["on_pace"] == 1
        assert len(body["players"]) == min(60, body["total"])
        assert body["model"]["auc"] > body["model"]["espn_auc"] and body["model"]["definition"]["min_gain"] == B.MIN_GAIN
        free = client.get(f"/api/leagues/{league_id}/breakouts", params={"status": "fa", "limit": 5}, headers=auth).json()
        assert free["total"] == 12 and len(free["players"]) == 5
        assert all(p["fantasy_team_id"] is None for p in free["players"])
        assert client.get(f"/api/leagues/{league_id}/breakouts", params={"status": "nope"}, headers=auth).status_code == 422
