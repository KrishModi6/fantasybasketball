"""xFP: NBA.com ids, the shot model, expected stats and how the engine uses them (offline)."""

from __future__ import annotations

import random

import numpy as np
import pytest

import fbb.xfp as X
from fbb.connectors.nba_stats import match_ids, name_key
from fbb.models import GameLog, SeasonLine, TrackingLine
from fbb.projections import ProjectionEngine

from factories import make_snapshot

LEAGUE = {"FTM": 0.78, "AST": 0.55, "REB": 0.58, "STL": 0.52, "BLK": 0.17}
ROW = {"GP": 10, "MIN": 300.0, "FGM": 60, "FGA": 120, "FG3M": 20, "FG3A": 50, "FTM": 25, "FTA": 30, "AST": 40,
       "REB": 50, "STL": 12, "BLK": 4, "TOV": 15, "POTENTIAL_AST": 60, "REB_CHANCES": 90, "DEFLECTIONS": 20,
       "CONTESTED_SHOTS_2PT": 30}


def _shots(n: int, seed: int = 1) -> list[list]:
    """Synthetic compact shots: layups go in 65% of the time, threes 35%."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        three = i % 2
        feet = rng.randint(23, 27) if three else rng.randint(0, 3)
        out.append([20261101 + i % 20, 1 + i % 5, "g", 1, 300, "Jump Shot" if three else "Layup Shot", three,
                    "Above the Break 3" if three else "Restricted Area", feet, 0, feet * 10,
                    int(rng.random() < (0.35 if three else 0.65))])
    return out


def test_name_key_drops_accents_punctuation_and_suffixes():
    assert name_key("Nikola Jokić") == name_key("Nikola Jokic") == "nikola jokic"
    assert name_key("A.J. Lawson") == name_key("AJ Lawson")
    assert name_key("Gary Payton II") == name_key("Gary Payton") and name_key("Jaren Jackson Jr.") == "jaren jackson"


def test_match_ids_uses_team_for_shared_names_and_nicknames():
    espn = {"1": ("Marcus Morris", "PHI"), "2": ("Marcus Morris", "NYK"), "3": ("Nic Claxton", "BKN"),
            "4": ("Jalen Williams", "OKC"), "5": ("Jaylin Williams", "OKC")}
    nba = {10: ("Marcus Morris Sr.", "NYK"), 11: ("Nicolas Claxton", "BKN"), 12: ("Jalen Williams", "OKC"),
           13: ("J. Williams", "OKC")}
    assert match_ids(nba, espn) == {10: "2", 11: "3", 12: "4"}  # two Williamses on OKC: no guess


def test_skills_start_at_the_league_rate_and_move_with_evidence():
    rookie = X.skills([], LEAGUE)
    assert rookie["FG"] == 0 and rookie["AST"] == pytest.approx(LEAGUE["AST"])
    passer = X.skills([({"POTENTIAL_AST": 1000, "AST": 700}, {"FGA": 0, "FGM": 0, "xFG": 0, "3PA": 0, "3PM": 0,
                                                            "x3P": 0})], LEAGUE)
    assert LEAGUE["AST"] < passer["AST"] < 0.70
    older = X.skills([(None, None), ({"POTENTIAL_AST": 1000, "AST": 700}, None)], LEAGUE)
    assert LEAGUE["AST"] < older["AST"] < passer["AST"]  # two seasons ago counts half


def test_tracking_line_prices_opportunities_and_keeps_attempts():
    skill = {**X.skills([], LEAGUE), "FG": 0.02}
    shots = {"FGA": 120, "FGM": 60, "xFG": 54.0, "3PA": 50, "3PM": 20, "x3P": 17.5}
    line = X.tracking_line(2027, ROW, shots, skill)
    exp = line.expected
    assert exp["FGM"] == pytest.approx((54 / 120 + 0.02) * 120)
    assert exp["3PM"] == pytest.approx(17.5)
    assert exp["AST"] == pytest.approx(60 * LEAGUE["AST"]) and exp["STL"] == pytest.approx(20 * LEAGUE["STL"])
    assert exp["FTM"] == pytest.approx(30 * LEAGUE["FTM"])
    assert exp["FGA"] == line.actual["FGA"] and exp["FTA"] == line.actual["FTA"] and exp["TO"] == line.actual["TO"]
    assert exp["PTS"] == pytest.approx(2 * (exp["FGM"] - exp["3PM"]) + 3 * exp["3PM"] + exp["FTM"])
    assert line.actual["PTS"] == 2 * 40 + 3 * 20 + 25


def test_calibrate_matches_this_seasons_makes():
    shots = _shots(400)
    probs = np.full(len(shots), 0.3)
    cal = X.calibrate(shots, probs, through=20261110)
    for three in (0, 1):
        idx = [i for i, s in enumerate(shots) if s[6] == three and s[0] <= 20261110]
        assert cal[idx].sum() == pytest.approx(sum(shots[i][11] for i in idx))


def test_shot_model_prices_layups_above_threes():
    model = X.ShotModel().fit(_shots(3000))
    layup, three = model.predict([_shots(2)[0], _shots(2)[1]])
    assert 0.5 < layup < 0.8 and 0.2 < three < 0.5
    again = X.ShotModel.from_dict(model.to_dict())
    assert again.predict(_shots(2)) == pytest.approx(model.predict(_shots(2)))


def _lucky_passer():
    snap = make_snapshot()
    p = snap.players["1"]
    games = [GameLog(season=2027, period=d, team=p.pro_team, minutes=30,
                     stats={"PTS": 20, "REB": 6, "AST": 9, "STL": 1, "BLK": 1, "TO": 2, "FGM": 8, "FGA": 16,
                            "FTM": 3, "FTA": 4, "3PM": 1, "3PA": 4}) for d in range(1, 9)]
    p.recent_games = games
    p.current = SeasonLine(season=2027, team=p.pro_team, gp=8, minutes=240,
                           stats={k: sum(g.stats[k] for g in games) for k in games[0].stats})
    actual = {**p.current.stats, "AST": 72.0}
    p.tracking = TrackingLine(season=2027, gp=8, minutes=240, actual=actual, expected={**actual, "AST": 40.0})
    snap.current_period = 10
    return snap


def test_engine_moves_this_seasons_evidence_toward_expected():
    snap = _lucky_passer()
    eng = ProjectionEngine(snap, model=None)
    eng.xfp_beta = {}
    raw = eng.project_all()["1"]
    eng.xfp_beta = {"AST": 0.5}
    half = eng.project_all()["1"]
    eng.xfp_beta = {"AST": 1.0}
    full = eng.project_all()["1"]
    assert full.per_game["AST"] < half.per_game["AST"] < raw.per_game["AST"]
    assert full.per_game["REB"] == pytest.approx(raw.per_game["REB"]) and full.mpg == pytest.approx(raw.mpg)
    assert full.xfp_pg == pytest.approx(full.tracked_fp_pg - 2 * 32 / 8)  # 32 fewer assists at 2 points each


def test_last_seasons_tracking_is_ignored():
    snap = _lucky_passer()
    snap.players["1"].tracking.season = 2026
    eng = ProjectionEngine(snap, model=None)
    eng.xfp_beta = {}
    raw = eng.project_all()["1"]
    eng.xfp_beta = {"AST": 1.0}
    pr = eng.project_all()["1"]
    assert pr.per_game["AST"] == pytest.approx(raw.per_game["AST"]) and pr.xfp_pg is None


def test_attach_tracking_links_players_offline(monkeypatch):
    snap = make_snapshot()
    pid, p = next(iter(snap.players.items()))
    shots = [[20261101, 77, "g", 1, 300, "Layup Shot", 0, "Restricted Area", 1, 0, 10, 1]] * 4
    model = X.XfpModel({"shot_model": X.ShotModel().fit(_shots(3000)).to_dict(), "beta": {}, "league": LEAGUE})
    monkeypatch.setattr(X.XfpModel, "load", classmethod(lambda cls, path=None: model))
    monkeypatch.setattr(X, "refresh_live", lambda season: {
        "dash": {"77": {**ROW, "name": p.name, "team": p.pro_team}}, "shots": shots})
    monkeypatch.setattr(X, "NBA_IDS_PATH", X.CACHE_DIR / "no-such-file.json")
    assert X.attach_tracking(snap.players, 2027) == 1
    assert p.tracking is not None and p.tracking.gp == ROW["GP"] and p.tracking.expected["AST"] == pytest.approx(33)
