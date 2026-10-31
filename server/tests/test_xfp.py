"""xFP: NBA.com ids, the shot model, expected stats and how the engine uses them (offline)."""

from __future__ import annotations

import gzip
import json
import random
from datetime import date, timedelta

import httpx
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


def test_calibrated_lines_add_up_to_what_happened_league_wide():
    # Last season's skills expect 40% more rebounds and free throws than the league got: that drift is nobody's luck.
    big = X.tracking_line(2027, {**ROW, "REB": 60, "REB_CHANCES": 100, "FTM": 20}, None, {**X.skills([], LEAGUE), "REB": 1.0})
    guard = X.tracking_line(2027, {**ROW, "REB": 10, "REB_CHANCES": 40, "FTM": 20}, None, {**X.skills([], LEAGUE), "REB": 1.0})
    before = big.expected["REB"] / guard.expected["REB"]
    lines = X.calibrate_lines({1: big, 2: guard})
    for stat in X.COMPONENTS:
        assert sum(x.expected[stat] for x in lines.values()) == pytest.approx(sum(x.actual[stat] for x in lines.values()))
    assert big.expected["REB"] / guard.expected["REB"] == pytest.approx(before)  # who got more chances still counts
    # The league got 70 rebounds from 140 chances: his 100 were worth 50, and he got 60.
    assert big.actual["REB"] - big.expected["REB"] == pytest.approx(10)
    e = big.expected
    assert e["PTS"] == pytest.approx(2 * (e["FGM"] - e["3PM"]) + 3 * e["3PM"] + e["FTM"])


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
    # Another NBA player, in nobody's league, still counts toward the league's assists per potential assist.
    other = {**ROW, "AST": 10, "POTENTIAL_AST": 40, "name": "Nobody Here", "team": "ZZZ"}
    monkeypatch.setattr(X, "refresh_live", lambda season: {
        "dash": {"77": {**ROW, "name": p.name, "team": p.pro_team}, "78": other}, "shots": shots})
    monkeypatch.setattr(X, "NBA_IDS_PATH", X.CACHE_DIR / "no-such-file.json")
    assert X.attach_tracking(snap.players, 2027) == 1
    # 50 assists from 100 potential assists league-wide: his 60 were worth 30.
    assert p.tracking is not None and p.tracking.gp == ROW["GP"] and p.tracking.expected["AST"] == pytest.approx(30)


# ---- live file: the published copy first, then NBA.com ----------------------

class FakeNba:
    """Stands in for NbaStatsClient: one dashboard row and no new shots; records where shots resumed."""

    def __init__(self, fail: bool = False):
        self.fail, self.calls, self.starts = fail, 0, []

    def dashboards(self, season, through):
        self.calls += 1
        if self.fail:
            raise httpx.ConnectTimeout("stats.nba.com did not answer")
        return {"77": ROW}

    def season_shots(self, season, through=None, start=None):
        self.starts.append(start)
        return []

    def close(self):
        pass


def _day_before(day: str) -> str:
    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def _live(through: str, **extra) -> dict:
    shot = [int(through.replace("-", "")), 77, "g", 1, 300, "Layup Shot", 0, "Restricted Area", 1, 0, 10, 1]
    return {"season": 2027, "through": through, "fetched": "2026-10-01T00:00:00+00:00", "dash": {"77": ROW},
            "shots": [shot], **extra}


@pytest.fixture
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "CACHE_DIR", tmp_path)
    return tmp_path


def test_published_copy_through_yesterday_means_no_nba_requests(live_dir, monkeypatch):
    y = X.live_through()
    monkeypatch.setattr(X, "fetch_cloud_live", lambda season, etag="": _live(y, etag='"v1"'))
    nba = FakeNba()
    live = X.refresh_live(2027, client=nba)
    assert live["through"] == y and nba.calls == 0
    assert X._load_live(2027)["etag"] == '"v1"'  # kept, so the next check can be a 304


def test_unchanged_published_copy_keeps_ours(live_dir, monkeypatch):
    y = X.live_through()
    X._save_live(2027, _live(y, etag='"v1"'))  # fetched long ago, so past LIVE_TTL
    sent = []
    monkeypatch.setattr(X, "fetch_cloud_live", lambda season, etag="": sent.append(etag) or {})
    nba = FakeNba()
    live = X.refresh_live(2027, client=nba)
    assert sent == ['"v1"'] and nba.calls == 0 and live["fetched"] > "2026-10-01"


def test_stale_published_copy_is_finished_from_nba(live_dir, monkeypatch):
    y = X.live_through()
    monkeypatch.setattr(X, "fetch_cloud_live", lambda season, etag="": _live(_day_before(y), etag='"v0"'))
    nba = FakeNba()
    live = X.refresh_live(2027, client=nba)
    assert nba.starts == [date.fromisoformat(y)]  # only the day the published copy lacks
    assert live["through"] == y and len(live["shots"]) == 1 and "etag" not in live


def test_without_either_source_an_older_copy_is_used(live_dir, monkeypatch):
    monkeypatch.setattr(X, "fetch_cloud_live", lambda season, etag="": None)
    with pytest.raises(httpx.ConnectTimeout):
        X.refresh_live(2027, client=FakeNba(fail=True))
    older = _day_before(X.live_through())
    X._save_live(2027, _live(older))
    assert X.refresh_live(2027, client=FakeNba(fail=True))["through"] == older


def test_publisher_skips_the_published_copy(live_dir, monkeypatch):
    monkeypatch.setattr(X, "fetch_cloud_live", lambda *a, **k: pytest.fail("read the published copy"))
    assert X.refresh_live(2027, client=FakeNba(), cloud=False)["through"] == X.live_through()


def _serve(monkeypatch, status: int, content: bytes = b"") -> list:
    sent = []

    def get(url, timeout=None, headers=None):
        sent.append((url, headers))
        return httpx.Response(status, content=content, headers={"ETag": '"v2"'}, request=httpx.Request("GET", url))

    monkeypatch.setattr(X, "TRACKING_URL", "https://tracking.test")
    monkeypatch.setattr(X.httpx, "get", get)
    return sent


def test_fetch_cloud_live_reads_the_gzipped_file(monkeypatch):
    sent = _serve(monkeypatch, 200, gzip.compress(json.dumps(_live("2026-10-28")).encode()))
    live = X.fetch_cloud_live(2027)
    assert live["through"] == "2026-10-28" and live["etag"] == '"v2"'
    assert sent == [("https://tracking.test/live_2027.json.gz", None)]
    _serve(monkeypatch, 304)
    assert X.fetch_cloud_live(2027, etag='"v2"') == {}


@pytest.mark.parametrize("status,content", [
    (403, b""),  # CloudFront's answer for a file that isn't there
    (200, b"not gzip"),
    (200, gzip.compress(b"[]")),
    (200, gzip.compress(json.dumps({**_live("2026-10-28"), "season": 2026}).encode())),
])
def test_fetch_cloud_live_ignores_missing_or_bad_files(monkeypatch, status, content):
    _serve(monkeypatch, status, content)
    assert X.fetch_cloud_live(2027) is None


def test_fetch_cloud_live_can_be_turned_off(monkeypatch):
    _serve(monkeypatch, 200, gzip.compress(json.dumps(_live("2026-10-28")).encode()))
    monkeypatch.setattr(X, "TRACKING_URL", "")
    assert X.fetch_cloud_live(2027) is None


def test_current_season_turns_over_in_august():
    assert X.current_season(date(2026, 10, 30)) == 2027
    assert X.current_season(date(2027, 4, 10)) == 2027 and X.current_season(date(2027, 8, 1)) == 2028
