"""The game predictor, on a made-up league (conftest.py) and on the model that ships with the repo."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from gamepred import __main__ as cli
from gamepred import data, features, model, predict, simulate
from gamepred.features import FEATURES, GROUPS, FeatureState

from conftest import HOME_EDGE, STAR_WORTH, STRENGTH, TEAMS

# ---- features: nothing from the future ------------------------------------------------------------------


def test_a_games_row_is_the_same_whatever_happens_after_it(logs, built):
    teams, players = logs
    games, _ = built
    cut = games["game_id"].iloc[199]
    early_teams = teams[teams["GAME_ID"] <= cut]
    early, _ = features.build(early_teams, players[players["GAME_ID"] <= cut])
    assert len(early) == 200
    pd.testing.assert_frame_equal(early[FEATURES], games[FEATURES].iloc[:200])
    # Change every later result: the first 200 rows still don't move.
    later = teams.copy()
    after = later["GAME_ID"] > cut
    later.loc[after, "PTS"] = later.loc[after, "PTS"].sample(frac=1.0, random_state=1).to_numpy()
    changed, _ = features.build(later, players)
    pd.testing.assert_frame_equal(changed[FEATURES].iloc[:200], games[FEATURES].iloc[:200])
    assert not changed[FEATURES].iloc[200:].reset_index(drop=True).equals(games[FEATURES].iloc[200:].reset_index(drop=True))


def test_the_first_game_knows_nothing_yet(built):
    games, _ = built
    first = games.iloc[0]
    assert first["elo_diff"] == 0 and first["games_home"] == 0 and first["net_diff"] == 0
    assert first["l10_win_diff"] == 0 and first["rest_home"] == 7 and first["missing_home"] == 0
    assert set(FEATURES) == {f for fs in GROUPS.values() for f in fs}  # every feature belongs to a factor
    assert not games[FEATURES].isna().any().any()


def test_ratings_find_the_strong_teams_and_carry_between_seasons(built):
    games, state = built
    elo = {state.teams[t].abbrev: state.teams[t].elo for t in TEAMS}
    assert sorted(elo, key=elo.get, reverse=True) == [TEAMS[t] for t in sorted(STRENGTH, key=STRENGTH.get, reverse=True)]
    opener = games[games["season"] == 2021].iloc[0]  # the best and worst teams are still far apart on opening night
    late = games[(games["season"] == 2021) & (games["home"] == "AAA") & (games["away"] == "FFF")].iloc[0]
    assert late["elo_diff"] > 150 and late["net_diff"] > 5
    assert opener["games_home"] == 0 and abs(opener["net_diff"]) > 0  # last season's ratings stand in at first
    assert games["home_win"].mean() > 0.5


def test_rest_and_back_to_backs():
    state = FeatureState()
    box = {"PTS": 110.0, "FGM": 40.0, "FG3M": 12.0, "FGA": 88.0, "FTA": 22.0, "TOV": 13.0, "OREB": 10.0, "DREB": 33.0}
    lose = {**box, "PTS": 100.0}
    for tid, name in TEAMS.items():
        state.team(tid, name)
    state.update(2024, date(2024, 11, 1), 1, 2, box, lose, False, [], [])
    state.update(2024, date(2024, 11, 2), 1, 3, box, lose, False, [], [])
    row = state.matchup(1, 4, date(2024, 11, 3), 2024)
    assert row["rest_home"] == 1 and row["b2b_home"] == 1 and row["g7_home"] == 2
    assert row["rest_away"] == 7 and row["b2b_away"] == 0 and row["g7_away"] == 0
    assert row["elo_diff"] > 0 and row["games_home"] == 2 and row["winpct_diff"] > 0
    assert state.matchup(1, 4, date(2024, 11, 6), 2024)["rest_home"] == 4


def test_a_missing_star_shows_in_the_row_and_an_unused_reserve_doesnt(logs, built):
    teams, players = logs
    games, _ = built
    season = games[games["games_home"] >= 5].set_index("game_id")  # once this season's rotations are established
    star = players[players["PLAYER_ID"] % 100 == 0].set_index(["GAME_ID", "TEAM_ID"])
    home_id = teams[teams["HOME"]].set_index("GAME_ID")["TEAM_ID"]
    out, there = [], []
    for gid, row in season.iterrows():
        (there if star.loc[(gid, home_id[gid]), "DRESSED"] else out).append(row)
    out, there = pd.DataFrame(out), pd.DataFrame(there)
    assert len(out) > 5 and len(there) > 20
    assert out["missing_home"].mean() > there["missing_home"].mean() + 5
    assert out["avail_home"].mean() < there["avail_home"].mean() - 5
    # The eighth man sits half the time by the coach's decision: healthy and in uniform, so never "missing".
    assert there["missing_home"].median() == 0


def test_team_abbreviations_from_other_sites_work():
    state = FeatureState()
    state.team(18, "NY")
    state.team(24, "SA")
    assert state.team_id("nyk") == 18 and state.team_id(" SAS ") == 24 and state.team_id("NY") == 18
    with pytest.raises(KeyError, match="No team 'XYZ'"):
        state.team_id("XYZ")
    assert features.name_key("Luka Dončić Jr.") == features.name_key("luka doncic") == "luka doncic"


# ---- data ------------------------------------------------------------------------------------------------


def _raw_team(**over) -> dict:
    row = {"game_id": 401, "season": 2026, "season_type": 2, "game_date": date(2025, 10, 21), "team_id": 25,
           "team_abbreviation": "OKC", "team_home_away": "home", "team_winner": True, "team_score": 125,
           "opponent_team_id": 10, "field_goals_made": 46, "field_goals_attempted": 104,
           "three_point_field_goals_made": 13, "three_point_field_goals_attempted": 52, "free_throws_made": 20,
           "free_throws_attempted": 25, "offensive_rebounds": 11, "defensive_rebounds": 27, "total_rebounds": 38,
           "assists": 29, "steals": 12, "blocks": 4, "turnovers": 11, "total_turnovers": 12.0, "fouls": 27}
    return {**row, **over}


def test_season_files_keep_regular_season_nba_games():
    raw = pd.DataFrame([_raw_team(), _raw_team(game_id=402, season_type=3), _raw_team(game_id=403, team_id=132),
                        _raw_team(game_id=404, total_turnovers=float("nan"))])
    tidy = data.tidy_team_box(raw)
    assert list(tidy["GAME_ID"]) == ["401", "404"]  # the playoff game and the All-Star team are gone
    first = tidy.iloc[0]
    assert first["SEASON"] == 2025 and first["GAME_DATE"] == "2025-10-21" and first["HOME"] and first["WIN"]
    assert first["PTS"] == 125 and first["FG3M"] == 13 and first["TOV"] == 12 and tidy.iloc[1]["TOV"] == 11
    assert list(tidy.columns) == data.TEAM_COLUMNS


def test_a_player_is_in_uniform_if_he_played_or_sat_by_the_coachs_decision():
    base = {"game_id": 401, "season": 2026, "season_type": 2, "game_date": date(2025, 10, 21), "team_id": 25,
            "team_abbreviation": "OKC", "field_goals_made": 9.0, "field_goals_attempted": 16.0,
            "three_point_field_goals_made": 0.0, "three_point_field_goals_attempted": 4.0, "free_throws_made": 5.0,
            "free_throws_attempted": 6.0, "offensive_rebounds": 0.0, "defensive_rebounds": 9.0, "rebounds": 9.0,
            "assists": 3.0, "steals": 0.0, "blocks": 0.0, "turnovers": 4.0, "fouls": 6.0, "points": 23.0}
    nothing = {k: float("nan") for k in base if isinstance(base[k], float)}
    raw = pd.DataFrame([
        {**base, "athlete_id": 1.0, "athlete_display_name": "Played", "minutes": 47.0, "did_not_play": False, "reason": None},
        {**base, **nothing, "athlete_id": 2.0, "athlete_display_name": "Coach", "minutes": float("nan"),
         "did_not_play": True, "reason": "COACH'S DECISION"},
        {**base, **nothing, "athlete_id": 3.0, "athlete_display_name": "Hurt", "minutes": float("nan"), "did_not_play": True,
         "reason": "LEFT ANKLE SPRAIN"},
        {**base, "athlete_id": float("nan"), "athlete_display_name": "Team", "minutes": 240.0, "did_not_play": False,
         "reason": None},
    ])
    tidy = data.tidy_player_box(raw)
    assert list(tidy["PLAYER"]) == ["Played", "Coach", "Hurt"]
    assert list(tidy["DRESSED"]) == [True, True, False] and list(tidy["MIN"]) == [47.0, 0.0, 0.0]
    assert tidy.iloc[0]["PTS"] == 23 and tidy.iloc[1]["PTS"] == 0 and tidy["PLAYER_ID"].dtype.kind == "i"


def test_an_espn_summary_reads_as_the_same_rows():
    stat = lambda name, value: {"name": name, "displayValue": value}  # noqa: E731
    team = lambda tid, abbrev, fg: {"team": {"id": str(tid), "abbreviation": abbrev}, "statistics": [  # noqa: E731
        stat("fieldGoalsMade-fieldGoalsAttempted", fg), stat("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "11-39"),
        stat("freeThrowsMade-freeThrowsAttempted", "27-31"), stat("totalRebounds", "52"), stat("offensiveRebounds", "16"),
        stat("defensiveRebounds", "36"), stat("assists", "23"), stat("steals", "6"), stat("blocks", "5"),
        stat("turnovers", "22"), stat("totalTurnovers", "25"), stat("fouls", "26")]}
    keys = ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
            "freeThrowsMade-freeThrowsAttempted", "rebounds", "assists", "turnovers", "steals", "blocks", "offensiveRebounds",
            "defensiveRebounds", "fouls", "plusMinus"]
    summary = {
        "header": {"competitions": [{"competitors": [{"team": {"id": "25"}, "score": "125"}, {"team": {"id": "10"}, "score": "124"}]}]},
        "boxscore": {"teams": [team(10, "HOU", "43-97"), team(25, "OKC", "46-104")], "players": [
            {"team": {"id": "10", "abbreviation": "HOU"}, "statistics": [{"keys": keys, "athletes": [
                {"athlete": {"id": "3202", "displayName": "Kevin Durant"}, "didNotPlay": False,
                 "stats": ["47", "23", "9-16", "0-4", "5-6", "9", "3", "4", "0", "0", "0", "9", "6", "+3"]},
                {"athlete": {"id": "7", "displayName": "Bench Guy"}, "didNotPlay": True, "reason": "COACH'S DECISION", "stats": []},
                {"athlete": {"id": "8", "displayName": "Hurt Guy"}, "didNotPlay": True, "reason": "RIGHT KNEE", "stats": []}]}]}]},
    }
    game = {"GAME_ID": "401", "GAME_DATE": "2025-10-21", "HOME_ID": 25}
    teams, players = data.parse_summary(summary, game, 2025)
    assert [(t["TEAM"], t["HOME"], t["WIN"], t["PTS"], t["FGA"], t["TOV"]) for t in teams] == [
        ("HOU", False, False, 124.0, 97.0, 25.0), ("OKC", True, True, 125.0, 104.0, 25.0)]
    assert [(p["PLAYER"], p["MIN"], p["PTS"], p["FGA"], p["DRESSED"]) for p in players] == [
        ("Kevin Durant", 47.0, 23.0, 16.0, True), ("Bench Guy", 0.0, 0.0, 0.0, True)]
    assert set(teams[0]) == set(data.TEAM_COLUMNS) and set(players[0]) == set(data.PLAYER_COLUMNS)


def test_seasons_are_named_by_the_year_they_start():
    assert data.season_label(2025) == "2025-26" and data.season_label(1999) == "1999-00"
    assert data.current_season(date(2026, 11, 11)) == 2026 and data.current_season(date(2027, 4, 1)) == 2026
    assert data.current_season(date(2027, 8, 1)) == 2026 and data.current_season(date(2027, 10, 20)) == 2027


# ---- simulation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("p", [0.15, 0.5, 0.72, 0.93])
def test_the_simulation_agrees_with_the_win_probability(p):
    sim = simulate.simulate(p, 226.0, 13.7, 19.0, 0.01, n=40_000, seed=3)
    assert sim["home_win"] == pytest.approx(p, abs=0.012)
    assert sim["home_score"] + sim["away_score"] == pytest.approx(226.0, abs=1.0)
    assert sim["home_range"][0] < sim["home_score"] < sim["home_range"][1]
    assert sim["margin"]["p10"] < sim["margin"]["p50"] < sim["margin"]["p90"]
    assert 0 < sim["overtime"] < 0.06 and sim["home_by_10"] + sim["away_by_10"] + sim["within_5"] < 1


def test_a_bigger_favourite_wins_by_more():
    close, heavy = (simulate.simulate(p, 226.0, 13.7, 19.0) for p in (0.55, 0.85))
    assert heavy["margin"]["mean"] > close["margin"]["mean"] + 8 and heavy["home_by_10"] > close["home_by_10"]
    assert simulate.consistent_margin(0.5, 13.7) == pytest.approx(0.0)
    assert simulate.consistent_margin(0.8, 13.7) == pytest.approx(-simulate.consistent_margin(0.2, 13.7))
    assert simulate.simulate(0.6, 226.0, 13.7, 19.0, n=5)["sims"] == 100  # never fewer than 100


# ---- models and predictions ------------------------------------------------------------------------------


def test_the_models_learn_the_made_up_league(built, bundle):
    games, _ = built
    bt = bundle["metrics"]["backtest"]
    assert bt["test_seasons"] == [2021] and bt["games"] == 60
    assert bt["overall"]["blend"]["log_loss"] < bt["overall"]["home"]["log_loss"]
    assert bt["overall"]["blend"]["accuracy"] > 0.6
    assert bt["margin_mae"] < bt["margin_mae_naive"]
    X = games[FEATURES].to_numpy(float)
    pred = model.predict(bundle["models"], X)
    assert ((pred["p"] > 0) & (pred["p"] < 1)).all() and pred["p"] == pytest.approx(0.5 * (pred["lr"] + pred["xgb"]))
    assert np.corrcoef(pred["margin"], games["margin"])[0, 1] > 0.5
    json.dumps(bt)  # plain numbers, ready to save


def test_a_prediction_has_odds_a_score_and_its_reasons(built, bundle):
    _, state = built
    g = predict.predict_game(bundle, state, "AAA", "FFF", date(2022, 3, 5), sims=4000)
    assert g["home"] == "AAA" and g["away"] == "FFF" and g["home_win_probability"] > 0.8
    assert g["home_win_probability"] + g["away_win_probability"] == pytest.approx(1.0)
    assert g["simulation"]["home_win"] == pytest.approx(g["home_win_probability"], abs=0.03)
    assert g["predicted_score"]["home"] > g["predicted_score"]["away"]
    names = [f["factor"] for f in g["factors"]]
    assert "Home court" in names and names[0] in ("Team strength (Elo)", "Season efficiency")
    assert next(f for f in g["factors"] if f["factor"] == "Home court")["points"] > 0
    assert len(g["lineups"]["home"]["playing"]) == 8 and g["lineups"]["home"]["playing"][0]["name"] == "AAA Player 0"
    flipped = predict.predict_game(bundle, state, "FFF", "AAA", date(2022, 3, 5), sims=4000)
    assert flipped["home_win_probability"] < 0.35
    assert flipped["home_win_probability"] > 1 - g["home_win_probability"]  # the same teams, with home court moved
    json.dumps(g)


def test_sitting_a_star_moves_the_odds_and_putting_him_back_restores_them(built, bundle):
    _, state = built
    day = date(2022, 3, 5)
    base = predict.predict_game(bundle, state, "CCC", "DDD", day)
    sat = predict.predict_game(bundle, state, "CCC", "DDD", day, out=["CCC Player 0"])
    assert sat["home_win_probability"] < base["home_win_probability"] - 0.05
    assert sat["features"]["missing_home"] > base["features"]["missing_home"] + 5
    assert sat["lineups"]["home"]["out"] == [{"name": "CCC Player 0", "value": pytest.approx(sat["lineups"]["home"]["out"][0]["value"]),
                                             "why": "what-if"}]
    other = predict.predict_game(bundle, state, "CCC", "DDD", day, out=["ddd player 0"])  # the visitors' star instead
    assert other["home_win_probability"] > base["home_win_probability"] + 0.05
    # The injury report sits him; a what-if puts him back.
    hurt = predict.predict_game(bundle, state, "CCC", "DDD", day, injuries={"CCC Player 0": "Out", "CCC Player 1": "Day-To-Day"})
    assert hurt["home_win_probability"] == pytest.approx(sat["home_win_probability"])
    assert hurt["lineups"]["home"]["out"][0]["why"] == "injury report"
    assert hurt["lineups"]["home"]["questionable"] == ["CCC Player 1"]  # listed, but counted as playing
    back = predict.predict_game(bundle, state, "CCC", "DDD", day, back=["CCC Player 0"], injuries={"CCC Player 0": "Out"})
    assert back["home_win_probability"] == pytest.approx(base["home_win_probability"])


def test_bad_requests_say_what_is_wrong(built, bundle):
    _, state = built
    with pytest.raises(ValueError, match="two different teams"):
        predict.predict_game(bundle, state, "AAA", "AAA")
    with pytest.raises(KeyError, match="No team"):
        predict.predict_game(bundle, state, "AAA", "ZZZ")
    with pytest.raises(ValueError, match="No player named 'Nobody'"):
        predict.predict_game(bundle, state, "AAA", "BBB", out=["Nobody"])
    with pytest.raises(ValueError, match="isn't on AAA or BBB"):
        predict.predict_game(bundle, state, "AAA", "BBB", back=["CCC Player 0"])
    with pytest.raises(ValueError, match="isn't on AAA or BBB"):  # another team's player never sits one of theirs
        predict.predict_game(bundle, state, "AAA", "BBB", out=["CCC Player 0"])


def test_a_surname_alone_works_only_when_it_can_mean_one_player(built, bundle):
    _, state = built
    day = date(2022, 3, 5)
    renamed = {101: "Jaylen Brown", 201: "Bruce Brown", 102: "Jayson Tatum"}
    old = {pid: state.players[pid].name for pid in renamed}
    try:
        for pid, name in renamed.items():
            state.players[pid].name = name
        sat = predict.predict_game(bundle, state, "AAA", "BBB", day, out=["tatum"])
        assert [p["name"] for p in sat["lineups"]["home"]["out"]] == ["Jayson Tatum"]
        full = predict.predict_game(bundle, state, "AAA", "BBB", day, out=["Bruce Brown Jr."])
        assert [p["name"] for p in full["lineups"]["away"]["out"]] == ["Bruce Brown"] and not full["lineups"]["home"]["out"]
        with pytest.raises(ValueError, match="No player named 'Brown'"):  # two Browns: it won't guess
            predict.predict_game(bundle, state, "AAA", "BBB", day, out=["Brown"])
        with pytest.raises(ValueError, match="No player named 'Jalen Brown'"):  # a full name has to be right
            predict.predict_game(bundle, state, "AAA", "BBB", day, out=["Jalen Brown"])
    finally:
        for pid, name in old.items():
            state.players[pid].name = name


def test_a_days_games_come_from_the_schedule(built, bundle):
    _, state = built
    schedule = pd.DataFrame([
        {"GAME_ID": "1", "GAME_DATE": "2022-03-05", "TIP": None, "HOME": "AAA", "AWAY": "BBB", "NEUTRAL": False, "STATUS": "scheduled"},
        {"GAME_ID": "2", "GAME_DATE": "2022-03-05", "TIP": None, "HOME": "CCC", "AWAY": "ZZZ", "NEUTRAL": False, "STATUS": "scheduled"},
        {"GAME_ID": "3", "GAME_DATE": "2022-03-06", "TIP": None, "HOME": "EEE", "AWAY": "FFF", "NEUTRAL": True, "STATUS": "scheduled"},
    ])
    games = predict.predict_day(bundle, state, date(2022, 3, 5), schedule)
    assert [(g["away"], g["home"], g["status"]) for g in games] == [("BBB", "AAA", "scheduled")]  # ZZZ isn't a team we know
    neutral = predict.predict_day(bundle, state, date(2022, 3, 6), schedule)[0]
    assert neutral["neutral"] and neutral["features"]["neutral"] == 1.0


def test_the_command_line(built, bundle, monkeypatch, capsys):
    _, state = built
    monkeypatch.setattr(model, "load", lambda path=None: bundle)
    monkeypatch.setattr(cli, "current_state", lambda refresh=False: state)
    monkeypatch.setattr(data, "load_injuries", lambda: {})
    assert cli.main(["predict", "--home", "AAA", "--away", "BBB", "--date", "2022-03-05", "--out", "AAA Player 0",
                     "--sims", "2000"]) == 0
    text = capsys.readouterr().out
    assert "BBB at AAA, 2022-03-05" in text and "points from the lineup change" in text
    assert "AAA out: AAA Player 0 (what-if)" in text and "Predicted score: AAA" in text and "Home court" in text
    assert cli.main(["predict", "--home", "AAA", "--away", "BBB", "--date", "2022-03-05", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["baseline"] is None and body["games"][0]["home"] == "AAA" and body["data_through"]
    assert cli.main(["predict", "--home", "AAA", "--away", "QQQ"]) == 2
    assert "No team 'QQQ'" in capsys.readouterr().err
    assert cli.main(["predict", "--out", "AAA Player 0"]) == 2
    assert "--home and --away" in capsys.readouterr().err
    assert cli.main(["lineup", "AAA"]) == 0
    assert "1. AAA Player 0" in capsys.readouterr().out


# ---- the model in the repo ---------------------------------------------------------------------------


def test_the_shipped_model_beat_the_simple_forecasts_in_its_backtest():
    bundle = model.load()
    bt = bundle["metrics"]["backtest"]
    overall = bt["overall"]
    assert bt["games"] > 7000 and len(bt["test_seasons"]) >= 6
    assert overall["home"]["accuracy"] < overall["elo"]["accuracy"] < overall["blend"]["accuracy"]
    assert overall["blend"]["log_loss"] < overall["elo"]["log_loss"] - 0.01 < overall["home"]["log_loss"]
    assert overall["blend"]["accuracy"] > 0.65
    done = [s for s in bt["test_seasons"] if bt["by_season"][str(s)]["games"] > 1000]
    assert all(bt["by_season"][str(s)]["blend"]["log_loss"] < bt["by_season"][str(s)]["elo"]["log_loss"] for s in done)
    assert bt["margin_mae"] < bt["margin_mae_naive"] and bt["total_mae"] < bt["total_mae_naive"]
    # The odds mean what they say: in each band, the home team won about as often as predicted.
    assert all(abs(band["predicted"] - band["home_won"]) < 0.04 for band in bt["calibration"])
    assert 12 < bundle["sigma_margin"] < 16 and 16 < bundle["sigma_total"] < 22 and abs(bundle["rho"]) < 0.2
