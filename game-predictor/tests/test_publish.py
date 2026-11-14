"""The published predictions file (gamepred/publish.py), on the made-up league."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from gamepred import __main__ as cli
from gamepred import data, model, publish


def _schedule(day: date) -> list[dict]:
    if day == date(2022, 3, 5):
        return [
            {"GAME_ID": "9001", "GAME_DATE": "2022-03-05", "TIP": "2022-03-06T00:00Z", "HOME": "AAA", "AWAY": "FFF",
             "HOME_ID": 1, "AWAY_ID": 6, "NEUTRAL": False, "STATUS": "final", "HOME_PTS": 121, "AWAY_PTS": 99},
            {"GAME_ID": "9002", "GAME_DATE": "2022-03-05", "TIP": "2022-03-06T02:30Z", "HOME": "CCC", "AWAY": "DDD",
             "HOME_ID": 3, "AWAY_ID": 4, "NEUTRAL": False, "STATUS": "scheduled", "HOME_PTS": None, "AWAY_PTS": None},
            {"GAME_ID": "9003", "GAME_DATE": "2022-03-05", "TIP": None, "HOME": "BBB", "AWAY": "ZZZ", "HOME_ID": 2,
             "AWAY_ID": 99, "NEUTRAL": False, "STATUS": "scheduled", "HOME_PTS": None, "AWAY_PTS": None},
        ]
    return []


def test_the_published_file_has_each_days_games_with_odds_scores_and_reasons(built, bundle):
    _, state = built
    now = datetime(2022, 3, 5, 15, 0, tzinfo=timezone.utc)
    out = publish.build(bundle, state, date(2022, 3, 5), 2, _schedule, {"CCC Player 0": "Out", "DDD Player 1": "Day-To-Day"},
                        sims=2000, now=now)
    assert out["format"] == publish.FORMAT and out["generated_at"] == "2022-03-05T15:00:00+00:00"
    assert out["injuries"] is True and out["data_through"] == str(state.last_day)
    assert out["backtest"]["games"] == 60 and 0 < out["backtest"]["home_accuracy"] < 1
    assert [(d["date"], len(d["games"])) for d in out["days"]] == [("2022-03-05", 2), ("2022-03-06", 0)]  # ZZZ isn't a team
    first, second = out["days"][0]["games"]
    assert (first["home"], first["away"], first["home_id"], first["away_id"]) == ("AAA", "FFF", 1, 6)
    assert first["status"] == "final" and (first["home_pts"], first["away_pts"]) == (121, 99)
    assert first["home_win"] > 0.8 and first["home_score"] > first["away_score"]
    assert first["home_range"][0] < first["home_score"] < first["home_range"][1]
    assert 1 <= len(first["factors"]) <= publish.TOP_FACTORS and {"factor", "points"} == set(first["factors"][0])
    assert second["status"] == "scheduled" and second["home_pts"] is None and second["tip"] == "2022-03-06T02:30Z"
    assert second["home_out"] == ["CCC Player 0"] and second["away_questionable"] == ["DDD Player 1"]
    assert json.loads(json.dumps(out)) == out  # plain data


def test_no_injury_report_is_said_not_hidden(built, bundle):
    _, state = built
    out = publish.build(bundle, state, date(2022, 3, 5), 1, _schedule, {}, sims=500)
    assert out["injuries"] is False and out["days"][0]["games"][1]["home_out"] == []


def test_the_publish_command_writes_the_file(built, bundle, monkeypatch, tmp_path, capsys):
    _, state = built
    monkeypatch.setattr(model, "load", lambda path=None: bundle)
    monkeypatch.setattr(cli, "current_state", lambda refresh=False: state)
    monkeypatch.setattr(data, "load_injuries", lambda: {})
    monkeypatch.setattr(data, "scoreboard", _schedule)
    target = tmp_path / "nested" / "predictions.json"
    assert cli.main(["publish", str(target), "--date", "2022-03-05", "--days", "1", "--sims", "500"]) == 0
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert [g["id"] for g in saved["days"][0]["games"]] == ["9001", "9002"]
    assert "2 games over 1 days" in capsys.readouterr().out


def _not_started(day: date) -> list[dict]:
    return [{**g, "STATUS": "scheduled", "HOME_PTS": None, "AWAY_PTS": None} for g in _schedule(day)]


def test_a_started_game_keeps_the_prediction_published_before_it(built, bundle):
    _, state = built
    day = date(2022, 3, 5)
    before = publish.build(bundle, state, day, 1, _not_started, {"AAA Player 0": "Out"}, sims=500)
    was = before["days"][0]["games"][0]
    assert was["status"] == "scheduled" and was["home_out"] == ["AAA Player 0"]
    # The next run: the game is over, the injury report has moved on, and the earlier file is handed in.
    after = publish.build(bundle, state, day, 1, _schedule, {}, sims=500, previous=json.loads(json.dumps(before)))
    first, second = after["days"][0]["games"]
    assert (first["status"], first["home_pts"], first["away_pts"]) == ("final", 121, 99)
    kept = ("home_win", "home_score", "away_score", "home_range", "factors", "home_out", "tip")
    assert {k: first[k] for k in kept} == {k: was[k] for k in kept}
    assert second["status"] == "scheduled" and second["id"] == "9002"  # still to come: predicted afresh


def test_a_started_game_with_no_earlier_prediction_is_left_out_once_the_results_reach_its_day(built, bundle):
    _, state = built
    day = state.last_day  # the results already cover this day, so a finished game could be among them

    def games(d: date) -> list[dict]:
        return [{**g, "GAME_DATE": str(day)} for g in _schedule(date(2022, 3, 5))] if d == day else []

    out = publish.build(bundle, state, day, 1, games, {}, sims=500)
    assert [g["id"] for g in out["days"][0]["games"]] == ["9002"]
    for junk in ({}, {"format": 99, "days": []}, {"format": publish.FORMAT, "days": [{"games": [{"id": "9001"}]}]}, "nope"):
        again = publish.build(bundle, state, day, 1, games, {}, sims=500, previous=junk)
        assert [g["id"] for g in again["days"][0]["games"]] == ["9002"]


def test_the_publish_command_reads_the_file_it_wrote_last_time(built, bundle, monkeypatch, tmp_path):
    _, state = built
    monkeypatch.setattr(model, "load", lambda path=None: bundle)
    monkeypatch.setattr(cli, "current_state", lambda refresh=False: state)
    monkeypatch.setattr(data, "load_injuries", lambda: {})
    target = tmp_path / "predictions.json"
    monkeypatch.setattr(data, "scoreboard", _not_started)
    assert cli.main(["publish", str(target), "--date", "2022-03-05", "--days", "1", "--sims", "500"]) == 0
    was = json.loads(target.read_text(encoding="utf-8"))["days"][0]["games"][0]
    monkeypatch.setattr(data, "load_injuries", lambda: {"AAA Player 0": "Out", "AAA Player 1": "Out"})
    monkeypatch.setattr(data, "scoreboard", _schedule)
    elsewhere = tmp_path / "next.json"
    args = ["publish", str(elsewhere), "--previous", str(target), "--date", "2022-03-05", "--days", "1", "--sims", "500"]
    assert cli.main(args) == 0
    now = json.loads(elsewhere.read_text(encoding="utf-8"))["days"][0]["games"][0]
    assert now["status"] == "final" and now["home_win"] == was["home_win"] and now["home_out"] == []
    assert publish.read(tmp_path / "missing.json") is None and publish.read(None) is None


def test_a_scoreboard_game_carries_its_score_once_it_has_started(monkeypatch):
    def event(eid, state, home_score, away_score):
        return {"id": eid, "date": "2026-11-14T00:00Z", "season": {"type": 2}, "status": {"type": {"state": state}},
                "competitions": [{"neutralSite": False, "competitors": [
                    {"homeAway": "home", "score": home_score, "team": {"id": "11", "abbreviation": "IND"}},
                    {"homeAway": "away", "score": away_score, "team": {"id": "18", "abbreviation": "NY"}}]}]}

    class Reply:
        def json(self):
            return {"events": [event("1", "post", "104", "117"), event("2", "in", "55", "48"), event("3", "pre", "0", "0"),
                               {**event("4", "pre", "0", "0"), "season": {"type": 3}}]}

    monkeypatch.setattr(data, "_get", lambda url, params=None, **kw: Reply())
    games = data.scoreboard(date(2026, 11, 13))
    assert [(g["GAME_ID"], g["STATUS"], g["HOME_PTS"], g["AWAY_PTS"]) for g in games] == [
        ("1", "final", 104, 117), ("2", "live", 55, 48), ("3", "scheduled", None, None)]  # the playoff game is left out
    assert games[0]["HOME"] == "IND" and games[0]["AWAY_ID"] == 18
    assert list(data.load_schedule(date(2026, 11, 13))["STATUS"]) == ["final", "live", "scheduled"]
