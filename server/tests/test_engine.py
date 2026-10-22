"""Offline unit tests for the analytics engine."""

from __future__ import annotations

import itertools
import random

import pytest

from fbb.baseline import FALLBACK_LEAGUE_RATES, RATE_STATS, aging_factor, per_game_from_rates, sps_projection
from fbb.connectors.espn import parse_settings
from fbb.connectors.fileimport import normalize_name, parse_upload
from fbb.context import AnalysisContext
from fbb.lineup import SlotModel, solve_day
from fbb.models import ScoringSettings
from fbb.projections import MANUAL_LIMIT, ProjectionEngine, games_from_injury_note
from fbb.strength import league_strength, round_robin
from fbb.trades import TradeEvaluator, apply_trade, find_trades

from factories import make_line, make_snapshot


# ---- scoring -----------------------------------------------------------------


def test_espn_default_points_scoring():
    # 25 pts on 10-20 FG, 2-3 3PT, 3-4 FT, 8 reb, 5 ast, 2 stl, 1 blk, 3 to
    line = {"PTS": 25, "FGM": 10, "FGA": 20, "3PM": 2, "FTM": 3, "FTA": 4, "REB": 8, "AST": 5, "STL": 2, "BLK": 1, "TO": 3}
    expected = 25 + 2 * 10 - 20 + 2 + 3 - 4 + 8 + 2 * 5 + 4 * 2 + 4 * 1 - 2 * 3
    assert ScoringSettings().fantasy_points(line) == expected


def test_derived_misses_are_scored():
    s = ScoringSettings(points={"FGMI": -1})
    assert s.fantasy_points({"FGM": 6, "FGA": 10}) == -4


# ---- baseline ------------------------------------------------------------------


def test_sps_with_no_history_is_league_average():
    b = sps_projection([], 2027, None, {})
    for k in RATE_STATS:
        assert b.rates[k] == pytest.approx(FALLBACK_LEAGUE_RATES[k])
    assert b.weighted_minutes == 0


def test_sps_regresses_small_samples_more():
    big = [make_line(s, 80, 34, 1.6) for s in (2024, 2025, 2026)]
    small = [make_line(2026, 6, 10, 1.6)]
    lg = {}
    b_big = sps_projection(big, 2027, 27, lg)
    b_small = sps_projection(small, 2027, 27, lg)
    lg_ast = FALLBACK_LEAGUE_RATES["AST"]
    # Same underlying talent; the small sample sits much closer to league average.
    assert abs(b_small.rates["AST"] - lg_ast) < abs(b_big.rates["AST"] - lg_ast)


def test_aging_curve_direction():
    assert aging_factor(22, "AST") > 1 > aging_factor(34, "AST")
    assert aging_factor(22, "TO") < 1  # bad stats move the other way
    assert aging_factor(None, "AST") == 1


def test_points_derived_from_makes():
    pg = per_game_from_rates({"FGM": 0.25, "3PM": 0.05, "FTM": 0.1, "FGMI": 0.2, "FTMI": 0.02, "3PMI": 0.1}, 30)
    assert pg["PTS"] == pytest.approx(2 * 7.5 + 1.5 + 3.0)
    assert pg["FGA"] == pytest.approx(7.5 + 6.0)


# ---- lineup optimiser ----------------------------------------------------------


def brute_force(cands, masks, n_slots):
    best = 0.0
    players = list(cands)
    for k in range(min(len(players), n_slots) + 1):
        for subset in itertools.combinations(players, k):
            for perm in itertools.permutations(range(n_slots), k):
                if all(masks[p] >> j & 1 for p, j in zip(subset, perm)):
                    best = max(best, sum(cands[p] for p in subset))
                    break
    return best


def test_lineup_matches_brute_force():
    slots = SlotModel({"PG": 1, "C": 1, "G": 1, "UT": 1, "BE": 3})
    rng = random.Random(1)
    positions = [["PG"], ["SG"], ["C"], ["PG", "SG"], ["SF", "PF"], ["C", "PF"]]
    for _ in range(60):
        n = rng.randint(1, 6)
        cands, masks = {}, {}
        for i in range(n):
            pos = rng.choice(positions)
            cands[i] = round(rng.uniform(5, 60), 1)
            masks[i] = slots.mask_for([], pos)
        got = solve_day([(i, cands[i], masks[i]) for i in cands], slots.n)
        assert len(set(got.values())) == len(got)  # one player per slot
        assert all(masks[p] >> j & 1 for p, j in got.items())  # eligibility respected
        assert sum(cands[p] for p in got) == pytest.approx(brute_force(cands, masks, slots.n))


# ---- injuries & adjustments -----------------------------------------------------


@pytest.mark.parametrize("note,expect", [
    ("He will be re-evaluated in 3-to-4 months.", (47, 52)),  # 105 days * 82/174
    ("Expected to miss 2 weeks with a sprained ankle", (6, 8)),
    ("Suffered a season-ending knee injury", (60, 60)),
    ("Questionable for Friday's game", None),
])
def test_injury_note_parsing(note, expect):
    got = games_from_injury_note(note, 60)
    if expect is None:
        assert got is None
    else:
        assert expect[0] <= got[0] <= expect[1]


def test_injury_reduces_games_not_rates():
    snap = make_snapshot(injured={"1": "OUT"})
    proj = ProjectionEngine(snap, model=None).project_all()
    healthy = ProjectionEngine(make_snapshot(), model=None).project_all()
    assert proj["1"].games_out > 0
    assert proj["1"].fp_pg == pytest.approx(healthy["1"].fp_pg, rel=0.1)
    ctx = AnalysisContext(snap, proj)
    assert ctx.expected_games("1") < AnalysisContext(make_snapshot(), healthy).expected_games("1")


def test_manual_override_is_bounded():
    from fbb.models import PlayerOverride

    base = ProjectionEngine(make_snapshot(), model=None).project_all()["2"]
    snap = make_snapshot()
    snap.overrides = [PlayerOverride(player_id="2", minutes_pct=0.9, note="huge role")]
    pr = ProjectionEngine(snap, model=None).project_all()["2"]
    assert pr.mpg / base.mpg <= 1 + MANUAL_LIMIT + 0.081  # manual cap plus at most the team-budget cap


# ---- calendar --------------------------------------------------------------------


def test_matchup_periods_are_monday_to_sunday(snapshot):
    mps = snapshot.settings.matchup_periods
    d = snapshot.schedule.period_dates
    assert d[mps[1][-1]].weekday() == 6
    for mp in range(2, 12):
        assert d[mps[mp][0]].weekday() == 0 and d[mps[mp][-1]].weekday() == 6
    assert max(mps) == 13  # 12 regular weeks + one playoff round


def test_round_robin_is_balanced():
    rounds = round_robin([1, 2, 3, 4, 5, 6])
    pairs = {frozenset(p) for r in rounds for p in r}
    assert len(rounds) == 5 and len(pairs) == 15


# ---- team strength & trades ------------------------------------------------------


def test_league_strength_probabilities(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    st = league_strength(ctx)
    assert sum(t.title_odds for t in st) == pytest.approx(1.0, abs=0.01)
    assert sum(t.playoff_odds for t in st) == pytest.approx(2.0, abs=0.02)
    assert st[0].team_id == 1  # the deliberately stronger team
    assert all(0 <= t.power_score <= 1 for t in st)


def test_two_for_one_drops_and_backfills(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    roster, drops, adds = apply_trade(ctx, 1, [a[0]], [b[0], b[1]], set())
    assert len(drops) == 1 and len(roster) == len(a)
    assert drops[0] not in (b[0], b[1])  # never drops a player just received
    roster_b, drops_b, adds_b = apply_trade(ctx, 2, [b[0], b[1]], [a[0]], set())
    assert len(adds_b) == 1 and len(roster_b) == len(b)
    assert ctx.s.players[adds_b[0]].fantasy_team_id is None


def test_trade_evaluation_shape_and_validation(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    r = ev.evaluate(1, 2, [a[0]], [b[0]], with_sim=False)
    assert {s["verdict"] for s in r["sides"]} <= {"advantageous", "neutral", "disadvantageous"}
    assert r["math"] and r["players"]
    with pytest.raises(ValueError):
        ev.evaluate(1, 2, [b[0]], [], with_sim=False)  # not on team 1's roster


def test_trade_finder_returns_gains(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    for s in find_trades(ctx, 2, candidates=40):
        assert s["my_delta_ros"] > 0
        assert s["partner_delta_pct"] >= -0.004


# ---- parsers -------------------------------------------------------------------------


def test_parse_espn_settings():
    payload = {"settings": {
        "name": "Hoops", "size": 10,
        "scoringSettings": {"scoringType": "H2H_POINTS", "scoringItems": [
            {"statId": 13, "points": 2}, {"statId": 14, "points": -1}, {"statId": 2, "points": 4}, {"statId": 11, "points": -2}]},
        "rosterSettings": {"lineupSlotCounts": {"0": 1, "4": 1, "11": 3, "12": 3, "13": 1, "14": 0}},
        "scheduleSettings": {"matchupPeriodCount": 19, "playoffTeamCount": 4, "playoffMatchupPeriodLength": 2,
                             "matchupPeriods": {"1": [1, 2, 3]}},
        "acquisitionSettings": {"matchupAcquisitionLimit": 7},
        "tradeSettings": {"deadlineDate": 1803052800000},
    }}
    s = parse_settings(payload, 2027)
    assert s.scoring.points == {"FGM": 2, "FGA": -1, "STL": 4, "TO": -2}
    assert s.roster_slots == {"PG": 1, "C": 1, "UT": 3, "BE": 3, "IR": 1}
    assert s.playoff_round_weeks == [2, 2] and s.acquisition_limit_per_matchup == 7
    assert s.trade_deadline is not None and s.trade_deadline.year == 2027


def test_parse_category_league():
    payload = {"settings": {"scoringSettings": {"scoringType": "H2H_CATEGORY", "scoringItems": [
        {"statId": 19}, {"statId": 20}, {"statId": 17}, {"statId": 6}, {"statId": 11, "isReverseItem": True}]}}}
    s = parse_settings(payload, 2027)
    assert s.scoring.kind == "categories" and s.scoring.categories == ["FG%", "FT%", "3PM", "REB", "TO"]


def test_csv_import_and_name_matching():
    spec = parse_upload("league.csv", b"team,owner,player\nA,Al,Luka Doncic\nA,Al,Jaren Jackson Jr.\nB,Bo,Nikola Jokic\n")
    assert [t["name"] for t in spec["teams"]] == ["A", "B"]
    assert normalize_name("Luka Dončić") == normalize_name("luka doncic")
    assert normalize_name("Jaren Jackson Jr.") == "jaren jackson"


def test_category_league_end_to_end():
    snap = make_snapshot()
    snap.settings.scoring = ScoringSettings(kind="categories", categories=["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"])
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    st = league_strength(ctx)
    assert sum(t.title_odds for t in st) == pytest.approx(1.0, abs=0.01)
    assert st[0].explanation["category_win_prob"]
    g = ctx.cat_valuer.gscores("1")
    assert set(g) == set(snap.settings.scoring.categories)


def test_espn_teams_keep_owner_ids():
    from fbb.connectors.espn import parse_teams

    data = {"members": [{"id": "{A-1}", "displayName": "Pat"}],
            "teams": [{"id": 4, "name": "Hoopers", "owners": ["{A-1}"]}, {"id": 5, "name": "Bricks"}]}
    hoopers, bricks = parse_teams(data)
    assert (hoopers.owner_name, hoopers.owner_ids) == ("Pat", ["{A-1}"])
    assert bricks.owner_ids == []


def test_trade_shortlist_is_not_one_deal_with_every_throw_in():
    from fbb.trades import TOP_SHARE, _shortlist

    # One deal plus every possible throw-in scores highest; real alternatives (2-for-2s, other partners) score lower.
    pre = [(100 - i, 2, ("star", f"filler{i}"), ("their_star",)) for i in range(60)]
    pre += [(30 - i, 3 + i % 4, (f"a{i}", f"b{i}"), (f"c{i}", f"d{i}")) for i in range(40)]
    pre.sort(key=lambda x: -x[0])
    short = _shortlist(pre, 40, 2, 2, n_partners=6)
    copies = [s for s in short if s[2] == ("their_star",)]
    assert len(short) == len(set(short)) == 40
    assert len(copies) <= int(40 * TOP_SHARE) + 2  # the reserved top scores, then at most 2 per exact package
    assert sum(len(give) == 2 == len(get) for _, give, get in short) >= 20
    assert short[0] == (2, ("star", "filler0"), ("their_star",))  # the best quick score is always simulated


def _calendar(season: dict):
    import datetime as dt

    from fbb.models import ProSchedule

    start = dt.date.fromisoformat(season["period_1"])
    counts = season["team_games"]
    return ProSchedule(season=start.year + 1,
                       period_dates={p: start + dt.timedelta(days=p - 1) for p in range(1, len(counts) + 1)},
                       games={p: ["T"] * n for p, n in enumerate(counts, 1) if n})


def test_espn_matchups_match_real_seasons():
    """ESPN lists matchup periods as weeks; rebuilding their days must reproduce ESPN's own dates."""
    import json
    from pathlib import Path

    from fbb.schedule import espn_matchup_periods

    seasons = json.loads((Path(__file__).parent / "data" / "espn_calendars.json").read_text())
    for name, season in seasons.items():
        if name.startswith("_"):
            continue
        weeks = {int(k): v for k, v in season["espn_weeks"].items()}
        days = espn_matchup_periods(_calendar(season), weeks)
        assert days is not None, f"{name}: a different number of weeks than ESPN's calendar"
        assert len(days) == len(weeks)
        for mp, (first, last) in season["espn_days"].items():
            played = [p for p in days[int(mp)] if season["team_games"][p - 1]]
            assert (played[0], played[-1]) == (first, last), f"{name} matchup {mp}"


def test_espn_matchups_2026_27_calendar():
    import datetime as dt
    import json
    from pathlib import Path

    from fbb.schedule import espn_matchup_periods

    season = json.loads((Path(__file__).parent / "data" / "espn_calendars.json").read_text())["2026-27"]
    sched = _calendar(season)
    days = espn_matchup_periods(sched, {int(k): v for k, v in season["espn_weeks"].items()})
    span = lambda mp: (sched.period_dates[days[mp][0]], sched.period_dates[days[mp][-1]])  # noqa: E731
    assert span(1) == (dt.date(2026, 10, 20), dt.date(2026, 10, 25))  # opening night to the first Sunday
    assert span(7) == (dt.date(2026, 11, 30), dt.date(2026, 12, 13))  # NBA Cup knockout weeks, merged
    assert span(17) == (dt.date(2027, 2, 15), dt.date(2027, 2, 28))  # All-Star break, merged
    assert span(21) == (dt.date(2027, 3, 29), dt.date(2027, 4, 11))  # final two-week playoff round


def test_espn_matchups_fall_back_when_weeks_disagree():
    import json
    from pathlib import Path

    from fbb.schedule import build_matchup_periods, espn_matchup_periods

    season = json.loads((Path(__file__).parent / "data" / "espn_calendars.json").read_text())["2026-27"]
    sched = _calendar(season)
    assert espn_matchup_periods(sched, {1: [1], 2: [2, 30]}) is None  # ESPN counting 30 weeks: not this season
    default = build_matchup_periods(sched, 19, [2, 2])
    assert len(default) == 21 and default[1][0] == 1
