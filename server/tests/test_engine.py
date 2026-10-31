"""Offline unit tests for the analytics engine."""

from __future__ import annotations

import itertools
import random
from datetime import datetime, time, timedelta, timezone

import pytest

from fbb.baseline import FALLBACK_LEAGUE_RATES, RATE_STATS, aging_factor, per_game_from_rates, sps_projection
from fbb.connectors.espn import EASTERN, parse_settings
from fbb.connectors.fileimport import normalize_name, parse_upload
from fbb.context import MISS_NEXT_FLOOR, MISS_NEXT_GAME, AnalysisContext
from fbb.enrich import enrich_players
from fbb.improve import add_drop_moves, streaming_moves
from fbb.lineup import SlotModel, solve_day
from fbb.models import GameLog, Player, RosterEntry, ScoringSettings, SeasonLine, TrackingLine
from fbb.projections import MANUAL_LIMIT, SEASON_ONLY_AFTER_GAMES, ProjectionEngine, games_from_injury_note
from fbb.schedule import GAME_LENGTH
from fbb.strength import MatchupModel, TeamStrength, league_strength, round_robin, team_windows
from fbb.trades import TradeEvaluator, TradeSide, _fairness, _verdict, apply_trade, find_trades, pickup_pool
from fbb.valuation import player_values

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


def test_sps_reads_2024_25_onward():
    recent = [make_line(2025, 70, 30, 1.0), make_line(2026, 70, 30, 1.0)]
    older = make_line(2024, 80, 36, 2.0)  # a much better 2023-24, which a 2026-27 projection ignores
    a = sps_projection(recent, 2027, 27, {})
    b = sps_projection([older] + recent, 2027, 27, {})
    assert b.rates == a.rates and b.mpg == a.mpg and b.availability == a.availability
    assert b.seasons_used == [2026, 2025]


def test_enrich_keeps_seasons_from_2024_25():
    lines = [make_line(s, 60, 30, 1.0) for s in (2022, 2023, 2024, 2025, 2026)]
    players = {"x": Player(id="x", name="Not in the dataset", history=lines),
               "3112335": Player(id="3112335", name="Nikola Jokic")}  # filled from the bundled history
    enrich_players(players, 2027)
    assert [h.season for h in players["x"].history] == [2025, 2026]
    assert [h.season for h in players["3112335"].history] == [2025, 2026]


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


def test_healthy_player_rarely_misses_his_next_game():
    """Availability is a season's worth of misses; the injuries behind his next few mostly haven't happened."""
    ctx = AnalysisContext(make_snapshot())
    pr = ctx.proj["1"]
    pp = [v for _, v in sorted(ctx.p_play("1").items())]
    assert pr.availability < 0.99
    miss = 1 - pr.availability
    assert pp[0] == pytest.approx(1 - min(miss, MISS_NEXT_FLOOR + MISS_NEXT_GAME * miss))
    assert pp[0] > pp[10] > pp[-1]
    assert pp[-1] == pytest.approx(pr.availability, abs=0.005)


def test_teammates_minutes_come_from_their_own_games():
    """No team-minutes budget: an injured teammate changes nobody else's projected minutes."""
    def team(injured=None):
        snap = make_snapshot(injured=injured)
        for k in "12345678":
            snap.players[k].pro_team = "BOS"
        return ProjectionEngine(snap, model=None).project_all()

    healthy, hurt = team(), team({"1": "OUT"})
    assert all(hurt[k].mpg == pytest.approx(healthy[k].mpg) for k in "2345678")


def test_manual_override_is_bounded():
    from fbb.models import PlayerOverride

    base = ProjectionEngine(make_snapshot(), model=None).project_all()["2"]
    snap = make_snapshot()
    snap.overrides = [PlayerOverride(player_id="2", minutes_pct=0.9, note="huge role")]
    pr = ProjectionEngine(snap, model=None).project_all()["2"]
    assert pr.mpg / base.mpg <= 1 + MANUAL_LIMIT + 1e-9


# ---- in-season update -------------------------------------------------------------

BIG_GAME = {"PTS": 42, "REB": 12, "AST": 9, "STL": 2, "BLK": 1, "TO": 2, "FGM": 15, "FGA": 25, "FTM": 8, "FTA": 9,
            "3PM": 4, "3PA": 9}


def _with_games(seasons: list[int]):
    snap = make_snapshot()
    snap.current_period = 40  # every logged game is over
    p = snap.players["1"]
    p.recent_games = [GameLog(season=s, period=160 + i if s == 2026 else 1 + i, team=p.pro_team, minutes=36,
                              stats=BIG_GAME) for i, s in enumerate(seasons)]
    return ProjectionEngine(snap, model=None).project_all()["1"]


def test_todays_game_counts_from_tomorrow():
    """A game in progress (in the log and in the season totals) must not read as a quiet full game."""
    snap = make_snapshot()
    snap.current_period = 5
    base = ProjectionEngine(snap, model=None).project_all()["1"]
    p = snap.players["1"]
    half = {"PTS": 6, "REB": 2, "AST": 1, "FGM": 3, "FGA": 6}
    p.recent_games = [GameLog(season=2027, period=5, team=p.pro_team, minutes=12, stats=half)]
    p.current = SeasonLine(season=2027, team=p.pro_team, gp=1, minutes=12, stats=half)
    pr = ProjectionEngine(snap, model=None).project_all()["1"]
    assert pr.fp_pg == pytest.approx(base.fp_pg)
    assert pr.games_played == 0 and pr.season_fp_pg is None and pr.in_season_weight == 0
    snap.current_period = 6  # the next morning it counts
    assert ProjectionEngine(snap, model=None).project_all()["1"].games_played == 1


def test_only_the_unplayed_part_of_todays_game_is_left():
    snap = make_snapshot()
    p = snap.players["1"]
    today = next(d for d in range(1, 30) if p.pro_team in snap.schedule.games[d])
    snap.current_period = today
    before = AnalysisContext(snap).p_play("1")[today]
    mpg = AnalysisContext(snap).proj["1"].mpg
    p.recent_games = [GameLog(season=2027, period=today, team=p.pro_team, minutes=mpg / 2, stats={"PTS": 8})]
    assert AnalysisContext(snap).p_play("1")[today] == pytest.approx(before / 2)


def _tip_today(snap, team: str | None = None) -> datetime:
    """Today's tip-off at 7 p.m. Eastern, for ``team`` or every team playing."""
    today = snap.current_period
    tip = datetime.combine(snap.schedule.period_dates[today], time(19), tzinfo=EASTERN)
    snap.schedule.team_tips = {today: {t: tip for t in snap.schedule.games[today] if team in (None, t)}}
    return tip


def test_a_finished_game_today_counts_for_nothing():
    # He sat, or what he scored is already in the matchup totals: either way none of it is still to come.
    snap = make_snapshot()
    p = snap.players["1"]
    snap.current_period = next(d for d in range(1, 30) if p.pro_team in snap.schedule.games[d])
    tip = _tip_today(snap, p.pro_team)
    snap.fetched_at = tip + timedelta(hours=1)
    assert AnalysisContext(snap).p_play("1")[snap.current_period] > 0
    snap.fetched_at = tip + GAME_LENGTH
    assert AnalysisContext(snap).p_play("1")[snap.current_period] == 0
    # Older snapshots have only the day's first and last tip-off: once the last game is over, so is his.
    snap.schedule.team_tips = {}
    snap.schedule.tipoffs = {snap.current_period: (tip - timedelta(hours=1), tip)}
    assert AnalysisContext(snap).p_play("1")[snap.current_period] == 0
    snap.fetched_at = tip + timedelta(hours=1)  # the late game is still on, so his may be too
    assert AnalysisContext(snap).p_play("1")[snap.current_period] > 0


def test_a_free_agent_cant_join_a_game_that_has_tipped_off():
    snap = make_snapshot()
    fa = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    snap.current_period = next(d for d in range(1, 30) if fa.pro_team in snap.schedule.games[d])
    tip = _tip_today(snap)
    snap.fetched_at = tip - timedelta(hours=1)
    assert AnalysisContext(snap).first_day(fa.id) == snap.current_period
    snap.fetched_at = tip + timedelta(minutes=30)
    ctx = AnalysisContext(snap)
    assert ctx.first_day(fa.id) == snap.current_period + 1
    assert ctx.first_day(ctx.team_roster(1)[0]) == snap.current_period  # a rostered player is in already


def test_a_waiver_pickup_plays_once_his_claim_clears():
    snap = make_snapshot()
    snap.current_period = 3  # a Wednesday: week 1 has days 3 to 7 left
    fa = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    _star(snap, fa.id)  # worth streaming
    fa.on_waivers = True
    assert AnalysisContext(snap).first_day(fa.id) == 5  # the league's 48-hour waiver period
    fa.waiver_clears = datetime.combine(snap.schedule.period_dates[6], time(3), tzinfo=EASTERN)
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    assert ctx.first_day(fa.id) == 6
    res = ctx.project_window([fa.id], [3, 4, 5, 6, 7])
    assert res.games[fa.id] == pytest.approx(ctx.expected_games(fa.id, [6, 7]))
    move = next(m for m in streaming_moves(ctx, 1)["moves"] if m["add"] == fa.id)
    assert move["on_waivers"] and move["games_this_week"] == pytest.approx(ctx.expected_games(fa.id, [6, 7]))
    # The player he replaces is dropped when the claim clears, so he plays until then.
    roster, days = ctx.team_roster(1), [3, 4, 5, 6, 7]
    dropped_now = ctx.project_window([p for p in roster if p != move["drop"]] + [fa.id], days, team_id=1).mean
    assert move["delta_this_week"] > dropped_now - ctx.project_window(roster, days, team_id=1).mean


def test_live_window_runs_from_first_tip_until_the_last_game_ends():
    from datetime import datetime, timezone

    from fbb.models import ProSchedule
    from fbb.schedule import GAME_LENGTH, live_window

    first, last = datetime(2026, 10, 23, 23, tzinfo=timezone.utc), datetime(2026, 10, 24, 2, 30, tzinfo=timezone.utc)
    sched = ProSchedule(season=2027, tipoffs={4: (first, last)})
    assert live_window(sched, 4) == (first, last + GAME_LENGTH)
    assert live_window(sched, 5) is None


def test_last_seasons_closing_games_count_before_this_season_starts():
    base = _with_games([])
    pr = _with_games([2026] * 15)
    assert pr.fp_pg > base.fp_pg
    assert pr.carryover_games == 10 and pr.carryover_weight > 0 and pr.in_season_weight == 0
    assert pr.recent_fp_pg == pytest.approx(ScoringSettings().fantasy_points(BIG_GAME))


def test_this_seasons_games_count_more_than_last_seasons():
    base = _with_games([]).fp_pg
    assert _with_games([2027]).fp_pg - base > 2 * (_with_games([2026]).fp_pg - base) > 0


def test_last_season_stops_counting_once_this_season_has_enough_games():
    big_per_min = ScoringSettings().fantasy_points(BIG_GAME) / 36
    pr = _with_games([2026] * 5 + [2027] * SEASON_ONLY_AFTER_GAMES)
    assert pr.fp_pg / pr.mpg == pytest.approx(big_per_min)  # no preseason prior left
    assert pr.in_season_weight == 1 and pr.carryover_games == 0 and pr.carryover_weight == 0
    early = _with_games([2026] * 5 + [2027] * (SEASON_ONLY_AFTER_GAMES - 1))
    assert early.fp_pg / early.mpg < big_per_min and early.carryover_games == 5


# ---- calendar --------------------------------------------------------------------


def test_matchup_periods_are_monday_to_sunday(snapshot):
    mps = snapshot.settings.matchup_periods
    d = snapshot.schedule.period_dates
    assert d[mps[1][-1]].weekday() == 6
    for mp in range(2, 12):
        assert d[mps[mp][0]].weekday() == 0 and d[mps[mp][-1]].weekday() == 6
    assert max(mps) == 13  # 12 regular weeks + one playoff round


def test_power_score_blends_record_by_season_played(snapshot):
    base = {ts.team_id: ts for ts in league_strength(AnalysisContext(snapshot), with_sim=False)}
    assert all(ts.power_score == ts.projected_allplay and ts.record_weight == 0 for ts in base.values())
    # Halfway through a 12-week season: the weakest projected team is 6-0, everyone else 0-6.
    weakest = min(base.values(), key=lambda ts: ts.projected_allplay).team_id
    for t in snapshot.teams:
        t.wins, t.losses = (6, 0) if t.id == weakest else (0, 6)
    ranked = league_strength(AnalysisContext(snapshot), with_sim=False)
    top = ranked[0]
    assert top.team_id == weakest and top.record_weight == pytest.approx(0.5) and top.record_pct == 1.0
    assert top.power_score == pytest.approx(0.5 * 1.0 + 0.5 * top.projected_allplay)
    assert top.projected_allplay == pytest.approx(base[weakest].projected_allplay)
    assert "6-0 record counts for 50%" in top.explanation["summary"]


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


def _start_week(snap, mp: int) -> None:
    snap.current_matchup_period = mp
    snap.current_period = snap.settings.matchup_periods[mp][0]


def _strength(snap):
    return {ts.team_id: ts for ts in league_strength(AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all()))}


def test_team_luck_is_its_players_luck_at_the_rate_they_start():
    snap = make_snapshot()
    actual = {"PTS": 160.0, "REB": 48.0, "AST": 72.0, "STL": 8.0, "BLK": 8.0, "TO": 16.0, "FGM": 64.0, "FGA": 128.0,
              "FTM": 24.0, "FTA": 32.0, "3PM": 8.0, "3PA": 32.0}
    # Team 1's first player: 32 assists more than his chances were worth over 8 games, 2 points each.
    snap.players["1"].tracking = TrackingLine(season=snap.settings.season, gp=8, minutes=240, actual=actual,
                                              expected={**actual, "AST": 40.0})
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    teams = {ts.team_id: ts for ts in league_strength(ctx, with_sim=False)}
    starts = next(p["starts"] for p in teams[1].explanation["players"] if p["player_id"] == "1")
    weeks = max(1, len(ctx.days)) / 7
    assert starts > 0
    assert teams[1].luck_ppw == pytest.approx(2 * 32 / 8 * starts / weeks)
    assert [p["player_id"] for p in teams[1].explanation["luck"]["players"]] == ["1"]
    assert teams[2].luck_ppw is None  # nobody on team 2 has tracking: no luck to show, not zero


def test_projected_records_add_up_to_each_teams_games():
    # Five teams: one sits out every week, so over 12 weeks some play 9 games and some 10.
    snap = make_snapshot(n_teams=5)
    scheduled = {t.id: 0 for t in snap.teams}
    for m in snap.matchups:
        scheduled[m.home_team_id] += 1
        scheduled[m.away_team_id] += 1
    assert set(scheduled.values()) == {9, 10}
    # Weeks 1 and 2 played: the home team won, except a tie in week 1's first game.
    _start_week(snap, 3)
    team = {t.id: t for t in snap.teams}
    for i, m in enumerate(m for m in snap.matchups if m.matchup_period < 3):
        home, away = team[m.home_team_id], team[m.away_team_id]
        if i == 0:
            m.winner, home.ties, away.ties = "TIE", home.ties + 1, away.ties + 1
        else:
            m.winner, home.wins, away.losses = "HOME", home.wins + 1, away.losses + 1
    for tid, ts in _strength(snap).items():
        assert ts.proj_games == scheduled[tid] and ts.byes == 12 - scheduled[tid]
        assert ts.proj_ties == team[tid].ties
        assert ts.proj_wins + ts.proj_losses + ts.proj_ties == pytest.approx(ts.proj_games)
        w, l, t = ts.proj_record()
        assert w + l + t == ts.proj_games and abs(w - ts.proj_wins) <= 0.5


def test_projected_record_rounds_wins_and_derives_losses():
    ts = TeamStrength(team_id=1, name="A", abbrev="A", owner=None, proj_wins=8.5, proj_losses=8.5, proj_games=17)
    assert ts.proj_record() == (9, 8, 0)  # not 9-9 from rounding both halves up
    ts = TeamStrength(team_id=1, name="A", abbrev="A", owner=None, proj_wins=9.5, proj_losses=5.5, proj_ties=1,
                      proj_games=16)
    assert ts.proj_record() == (10, 5, 1)


def test_playoff_seeds_go_by_win_percentage():
    # Five teams over 12 weeks: two have an extra bye. A 6-2-1 team (.722) is seeded ahead of two 7-3 teams
    # (.700) though it has fewer wins; counting wins gave every extra game away a playoff spot.
    snap = make_snapshot(n_teams=5)
    scheduled = {t.id: 0 for t in snap.teams}
    for m in snap.matchups:
        scheduled[m.home_team_id] += 1
        scheduled[m.away_team_id] += 1
    nine = [tid for tid, n in scheduled.items() if n == 9]
    ten = [tid for tid, n in scheduled.items() if n == 10]
    _start_week(snap, 13)  # the regular season is over
    records = {nine[0]: (6, 2, 1), nine[1]: (2, 6, 1), ten[0]: (7, 3, 0), ten[1]: (7, 3, 0), ten[2]: (1, 9, 0)}
    for t in snap.teams:
        t.wins, t.losses, t.ties = records[t.id]
    st = _strength(snap)
    assert st[nine[0]].playoff_odds == 1.0
    assert st[ten[0]].playoff_odds + st[ten[1]].playoff_odds == pytest.approx(1.0)


def test_a_played_week_without_a_result_still_counts():
    # A demo league (and ESPN until a week's results post): week 1 is over with no result, every record 0-0.
    snap = make_snapshot()
    _start_week(snap, 2)
    assert all(ts.proj_games == 12 and ts.byes == 0 for ts in _strength(snap).values())
    # A file import's records already count week 1, which still has no result: it isn't counted twice.
    team = {t.id: t for t in snap.teams}
    for m in snap.matchups:
        if m.matchup_period == 1:
            team[m.home_team_id].wins += 1
            team[m.away_team_id].losses += 1
    assert all(ts.proj_games == 12 for ts in _strength(snap).values())


def test_a_played_weeks_final_score_decides_it_before_the_result_posts():
    snap = make_snapshot()
    _start_week(snap, 2)
    m = next(m for m in snap.matchups if m.matchup_period == 1)
    m.home_score, m.away_score = 900.0, 1000.0
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    mm = MatchupModel(ctx, team_windows(ctx))
    assert mm.p_win(m.home_team_id, m.away_team_id, 1) < 0.001


def test_two_for_one_drops_and_backfills(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    roster, drops, adds = apply_trade(ctx, 1, [a[0]], [b[0], b[1]])
    assert len(drops) == 1 and len(roster) == len(a)
    assert drops[0] not in (b[0], b[1])  # never drops a player just received
    roster_b, drops_b, adds_b = apply_trade(ctx, 2, [b[0], b[1]], [a[0]])
    assert len(adds_b) == 1 and len(roster_b) == len(b)
    assert ctx.s.players[adds_b[0]].fantasy_team_id is None
    assert adds_b == pickup_pool(ctx)[:1]  # the best free agent after the top FA_SKIP, who go fast


def test_an_open_spot_is_filled_before_the_trade():
    snap = make_snapshot(injured={"1": "OUT"})  # team 1's IR slot can take him, so it has a spot to fill
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    _, fills, _ = ev.baseline(1)
    assert fills == pickup_pool(ctx)[:1]
    # Getting two for one, the extra player takes that spot: the pickup it replaces is what he costs.
    r = ev.evaluate(1, 2, [a[1]], [b[0], b[1]], with_sim=False)
    me = r["sides"][0]
    assert me["open_spot_adds"] == fills and not me["auto_drops"] and not me["auto_adds"]
    ros = lambda p: ev.values[p]["ros_value"]  # noqa: E731
    assert me["raw_value_delta"] == pytest.approx(ros(b[0]) + ros(b[1]) - ros(a[1]) - ros(fills[0]))
    assert any("open roster spot" in line for line in r["math"][0]["lines"])


def test_a_player_below_a_pickup_counts_as_one(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    weak = min(a, key=lambda p: ev.values[p]["ros_value"])
    ev.replacement = ev.values[weak]["ros_value"] + 1  # he now projects below a pickup
    r = ev.evaluate(1, 2, [weak], [b[0]], with_sim=False)
    me = r["sides"][0]
    fa = pickup_pool(ctx)[0]
    assert me["counted_as_pickups"] == [[weak, fa]]  # trading him is compared with swapping him for a pickup
    assert me["raw_value_delta"] == pytest.approx(ev.values[b[0]]["ros_value"] - ev.values[fa]["ros_value"])
    assert any("counts as that pickup" in line for line in r["math"][0]["lines"])
    # The calculation also says what the trade does against the roster as it is.
    as_is = ev.baseline(1)[2]
    weeks = max(1.0, len(ctx.days) / 7)
    assert me["delta_vs_roster_per_week"] == pytest.approx((me["after_ros"] - as_is.mean) / weeks)
    assert any("Against the roster as it is" in line for line in r["math"][0]["lines"])


def test_getting_an_injured_player_opens_no_spot():
    snap = make_snapshot(injured={"9": "OUT"})  # team 2's first player
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a = ctx.team_roster(1)
    r = ev.evaluate(1, 2, [a[0]], ["9"], with_sim=False)
    me, them = r["sides"]
    assert not me["auto_adds"] and not me["auto_drops"]  # his IR slot isn't a free pickup
    assert them["open_spot_adds"] and not them["auto_adds"]  # team 2's open spot was there either way


def test_a_day_to_day_player_in_the_ir_slot_keeps_it():
    # ESPN leaves a player in the IR slot after he's upgraded to day-to-day: his team isn't over the limit,
    # so a trade mustn't drop someone for it (this cost a 2-for-2 50 points a week).
    snap = make_snapshot()
    fa = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    fa.fantasy_team_id, fa.injury_status = 2, "DAY_TO_DAY"
    snap.team(2).roster.append(RosterEntry(player_id=fa.id, slot="IR"))
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    r = ev.evaluate(1, 2, [a[0], a[1]], [b[0], b[1]], with_sim=False)
    them = r["sides"][1]
    assert not them["auto_drops"] and not them["auto_adds"] and not them["open_spot_adds"]
    # Trading him away takes the spot with him, though: then his old team has to drop a player.
    r = ev.evaluate(1, 2, [a[0]], [fa.id], with_sim=False)
    me, them = r["sides"]
    assert not me["auto_drops"] and them["auto_drops"]
    # And the team that gets him can't put a day-to-day player on IR, so two for one costs it a drop.
    r = ev.evaluate(1, 2, [a[0]], [fa.id, b[0]], with_sim=False)
    assert len(r["sides"][0]["auto_drops"]) == 1


def test_an_injured_player_got_two_for_one_takes_the_empty_ir_slot():
    snap = make_snapshot(injured={"9": "OUT"})  # team 2's first player; team 1's IR slot is empty
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), [p for p in ctx.team_roster(2) if p != "9"]
    r = ev.evaluate(1, 2, [a[0]], ["9", b[0]], with_sim=False)
    me = r["sides"][0]
    assert not me["auto_drops"] and not me["auto_adds"]  # he goes on IR, as he would in ESPN


def _star(snap, pid: str) -> None:
    p = snap.players[pid]
    p.history = [make_line(s, 75, 34, 1.5, p.pro_team) for s in (2024, 2025, 2026)]


def test_an_injured_player_got_two_for_one_costs_a_drop_when_he_returns():
    # He holds the empty IR slot only while he's out: the day he's back team 1 is a player over and drops its
    # weakest. This was a 50-point-a-week phantom in a real league's trade suggestions.
    snap = make_snapshot(injured={"9": "OUT"})
    _star(snap, "9")
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    b = [p for p in ctx.team_roster(2) if p != "9"]
    r = TradeEvaluator(ctx).evaluate(1, 2, [ctx.team_roster(1)[0]], ["9", b[0]], with_sim=False)
    back = min(d for d, v in ctx.p_play("9").items() if v > 0)
    [(dropped, returning, day)] = r["sides"][0]["return_drops"]
    assert returning == "9" and day == snap.schedule.period_dates[back].isoformat()
    assert dropped != "9"  # the star stays, the weakest goes (here the other player team 1 got)
    assert any(f"When Player 9 comes back on {day}" in line for line in r["math"][0]["lines"])


def test_a_returning_ir_player_costs_his_team_its_weakest_player():
    # Team 2 is full with an injured player in its IR slot. The day he's back it's a player over the limit, so
    # from then on it plays without its weakest player; it used to play all nine the rest of the season.
    snap = make_snapshot()
    hurt = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    hurt.fantasy_team_id, hurt.injury_status = 2, "OUT"
    snap.team(2).roster.append(RosterEntry(player_id=hurt.id, slot="IR"))
    _star(snap, hurt.id)
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    roster = ctx.team_roster(2)
    back = min(d for d, v in ctx.p_play(hurt.id).items() if v > 0)
    later = [d for d in ctx.days if d >= back]
    weakest = min((p for p in roster if p != hurt.id),
                  key=lambda p: ctx.value_pg[p] * ctx.expected_games(p, later))
    assert ctx.roster_cuts(2, roster) == {weakest: (back, hurt.id)}
    res = ctx.project_window(roster, team_id=2)
    assert res.games.get(weakest, 0) == pytest.approx(ctx.expected_games(weakest, [d for d in ctx.days if d < back]))
    assert res.mean < ctx.project_window(roster).mean
    assert ctx.roster_cuts(1, ctx.team_roster(1)) == {}  # a team within its limit keeps everyone


def test_pickups_never_drop_the_player_in_the_ir_slot():
    # Dropping him frees only the IR slot, which a healthy pickup can't take.
    snap = make_snapshot()
    fa, pickup = [p for p in snap.players.values() if p.fantasy_team_id is None][:2]
    fa.fantasy_team_id, fa.injury_status = 2, "DAY_TO_DAY"
    snap.team(2).roster.append(RosterEntry(player_id=fa.id, slot="IR"))
    _star(snap, pickup.id)  # a free agent worth adding
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    values = player_values(ctx)
    assert fa.id == min(ctx.team_roster(2), key=lambda p: values[p]["ros_value"])  # otherwise the obvious drop
    moves = add_drop_moves(ctx, 2, ctx.project_window(ctx.team_roster(2), team_id=2))
    moves += streaming_moves(ctx, 2)["moves"]
    assert moves and all(m["drop"] != fa.id for m in moves)


def test_a_team_already_over_its_limit_isnt_charged_a_drop_by_a_trade():
    snap = make_snapshot()
    fa = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    fa.fantasy_team_id = 2
    snap.team(2).roster.append(RosterEntry(player_id=fa.id))  # a player more than its slots, on the bench
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    r = ev.evaluate(1, 2, [a[0]], [b[0]], with_sim=False)
    assert not r["sides"][1]["auto_drops"]


def test_an_even_trade_drops_nobody_unless_an_ir_spot_leaves():
    # Every 1-for-1 between every two teams, with OUT players about and a day-to-day one in team 2's IR slot:
    # only trading him away for a player who can't take that IR slot costs a drop.
    snap = make_snapshot(injured={"1": "OUT", "20": "OUT"})
    fa = next(p for p in snap.players.values() if p.fantasy_team_id is None)
    fa.fantasy_team_id, fa.injury_status = 2, "DAY_TO_DAY"
    snap.team(2).roster.append(RosterEntry(player_id=fa.id, slot="IR"))
    ctx = AnalysisContext(snap, ProjectionEngine(snap, model=None).project_all())
    pool = pickup_pool(ctx)
    for t, u in itertools.permutations([t.id for t in snap.teams], 2):
        for p, q in itertools.product(ctx.team_roster(t), ctx.team_roster(u)):
            _, drops, adds = apply_trade(ctx, t, [p], [q], pool)
            spot_leaves = p == fa.id and snap.players[q].injury_status != "OUT"
            assert not adds and bool(drops) == spot_leaves, (t, p, q, drops)


def test_trade_evaluation_shape_and_validation(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b = ctx.team_roster(1), ctx.team_roster(2)
    r = ev.evaluate(1, 2, [a[0]], [b[0]], with_sim=False)
    assert {s["verdict"] for s in r["sides"]} <= {"advantageous", "neutral", "disadvantageous"}
    assert r["math"] and r["players"]
    with pytest.raises(ValueError):
        ev.evaluate(1, 2, [b[0]], [], with_sim=False)  # not on team 1's roster


def test_fairness_label_follows_verdicts():
    def label(*pcts):
        sides = [TradeSide(i, f"T{i}", [], [], delta_ros=p * 1000, delta_pct=p, verdict=_verdict(p)) for i, p in enumerate(pcts)]
        return _fairness(sides)["label"]

    assert label(-0.025, 0.008) == "favors T1"  # one side clearly loses: not "balanced"
    assert label(0.004, -0.003) == "balanced"
    assert label(0.02, 0.001) == "win-win"
    assert label(-0.002, -0.015) == "lose-lose"
    assert label(0.0, -0.02) == "favors T0"
    assert label(0.006, 0.003, -0.02) == "favors T0 and T1"


def test_three_team_trade(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    a, b, c = ctx.team_roster(1), ctx.team_roster(2), ctx.team_roster(3)
    r = ev.evaluate_multi({1: {a[0]: 2}, 2: {b[0]: 3}, 3: {c[0]: 1, c[1]: 2}}, with_sim=False)
    assert r["teams"] == [1, 2, 3] and len(r["sides"]) == 3 and len(r["math"]) == 3
    side = {s["team_id"]: s for s in r["sides"]}
    assert side[1]["receives"] == [c[0]] and side[2]["receives"] == [a[0], c[1]] and side[3]["receives"] == [b[0]]
    assert len(side[3]["auto_adds"]) == 1  # sends two, gets one: picks up a free agent
    assert {"player_id": c[1], "from_team": 3, "to_team": 2} in r["moves"]
    assert r["math"][0]["lines"][0].startswith(f"Sends {ctx.proj[a[0]].name} to {ctx.s.team(2).name}; gets")
    label = r["fairness"]["label"]
    assert label in ("win-win-win", "lose-lose-lose", "balanced") or label.startswith("favors ")
    assert 0 <= r["fairness"]["partner_accept_likelihood"] <= 1
    with pytest.raises(ValueError, match="another team"):
        ev.evaluate_multi({1: {a[0]: 1}, 2: {b[0]: 3}, 3: {c[0]: 1}}, with_sim=False)
    with pytest.raises(ValueError, match="must send or get"):
        ev.evaluate_multi({1: {a[0]: 2}, 2: {b[0]: 1}, 3: {}}, with_sim=False)
    with pytest.raises(ValueError, match="not on the giving roster"):
        ev.evaluate_multi({1: {b[1]: 2}, 2: {b[0]: 3}, 3: {c[0]: 1}}, with_sim=False)


def test_trade_finder_returns_gains(snapshot):
    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    for s in find_trades(ctx, 2, candidates=40):
        assert s["my_delta_ros"] > 0
        assert s["partner_delta_pct"] >= -0.004


def test_trade_finder_exact_package_size(snapshot):
    from collections import Counter

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    checked: Counter = Counter()
    rows = find_trades(ctx, 2, max_give=2, max_get=2, candidates=40, checked=checked, exact=True)
    assert set(checked) == {"2-for-2"}  # no 1-for-1, 2-for-1 or 1-for-2 is even simulated
    assert all(len(s["give"]) == len(s["get"]) == 2 for s in rows)


# ---- parsers -------------------------------------------------------------------------


def test_parse_espn_settings():
    payload = {"settings": {
        "name": "Hoops", "size": 10,
        "scoringSettings": {"scoringType": "H2H_POINTS", "scoringItems": [
            {"statId": 13, "points": 2}, {"statId": 14, "points": -1}, {"statId": 2, "points": 4}, {"statId": 11, "points": -2}]},
        "rosterSettings": {"lineupSlotCounts": {"0": 1, "4": 1, "11": 3, "12": 3, "13": 1, "14": 0}},
        "scheduleSettings": {"matchupPeriodCount": 19, "playoffTeamCount": 4, "playoffMatchupPeriodLength": 2,
                             "matchupPeriods": {"1": [1, 2, 3]}},
        "acquisitionSettings": {"matchupAcquisitionLimit": 7, "waiverHours": 24},
        "tradeSettings": {"deadlineDate": 1803052800000},
    }}
    s = parse_settings(payload, 2027)
    assert s.scoring.points == {"FGM": 2, "FGA": -1, "STL": 4, "TO": -2}
    assert s.roster_slots == {"PG": 1, "C": 1, "UT": 3, "BE": 3, "IR": 1}
    assert s.playoff_round_weeks == [2, 2] and s.acquisition_limit_per_matchup == 7 and s.waiver_hours == 24
    assert s.trade_deadline is not None and s.trade_deadline.year == 2027


def test_parse_category_league():
    payload = {"settings": {"scoringSettings": {"scoringType": "H2H_CATEGORY", "scoringItems": [
        {"statId": 19}, {"statId": 20}, {"statId": 17}, {"statId": 6}, {"statId": 11, "isReverseItem": True}]}}}
    s = parse_settings(payload, 2027)
    assert s.scoring.kind == "categories" and s.scoring.categories == ["FG%", "FT%", "3PM", "REB", "TO"]
    assert s.waiver_hours == 48  # ESPN's default when the league doesn't say


def test_parse_espn_tipoffs_and_waiver_dates():
    from fbb.connectors.espn import ESPN_PRO_TEAMS, parse_player, parse_pro_schedule

    def ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    early = datetime(2026, 10, 23, 23, tzinfo=timezone.utc)
    late = early + timedelta(hours=3)
    sched = parse_pro_schedule({"settings": {"proTeams": [
        {"id": 1, "proGamesByScoringPeriod": {"4": [{"date": ms(early)}]}},
        {"id": 2, "proGamesByScoringPeriod": {"4": [{"date": ms(late)}]}}]}}, 2027)
    assert sched.team_tips[4] == {ESPN_PRO_TEAMS[1]: early, ESPN_PRO_TEAMS[2]: late}
    assert sched.tipoffs[4] == (early, late)
    clears = datetime(2026, 10, 25, 7, tzinfo=timezone.utc)  # 3 a.m. Eastern
    p = parse_player({"player": {"id": 5}, "status": "WAIVERS", "waiverProcessDate": ms(clears)}, 2027)
    assert p.on_waivers and p.waiver_clears == clears and p.waiver_clears.date().day == 25
    assert parse_player({"player": {"id": 6}, "status": "FREEAGENT"}, 2027).waiver_clears is None


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
    short = _shortlist(pre, 40, 4, n_partners=6)
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


def test_trade_finder_searches_only_the_sizes_asked_for(snapshot):
    from collections import Counter

    from fbb.trades import EVEN_SIZES

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    checked: Counter = Counter()
    rows = find_trades(ctx, 2, candidates=40, sizes=EVEN_SIZES, checked=checked)
    assert checked and set(checked) <= {"1-for-1", "2-for-2"}  # no 2-for-1 or 1-for-2 is even simulated
    assert all(len(r["give"]) == len(r["get"]) for r in rows)
    checked.clear()
    find_trades(ctx, 2, candidates=40, sizes=frozenset({(2, 1)}), checked=checked)
    assert set(checked) == {"2-for-1"}
    checked.clear()
    rows = find_trades(ctx, 2, candidates=40, sizes=frozenset({(3, 3)}), checked=checked)
    assert set(checked) == {"3-for-3"}
    assert rows and all(len(r["give"]) == len(r["get"]) == 3 for r in rows)


def test_offer_finder_prices_the_players_you_pick(snapshot):
    from collections import Counter

    from fbb.trades import find_offers

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    target = ctx.team_roster(3)[0]
    checked: Counter = Counter()
    r = find_offers(ctx, 1, [target], ev, checked=checked)
    assert r["partner_id"] == 3 and r["get"] == [target]
    assert set(checked) <= {"1-for-1", "2-for-1", "3-for-1"}
    assert r["offers"] or r["closest"]
    gains = [o["my_delta_ros"] for o in r["offers"]]
    assert gains == sorted(gains, reverse=True)  # best for you first
    mine = set(ctx.team_roster(1))
    assert all(set(o["give"]) <= mine and o["partner_delta_pct"] >= -0.004 for o in r["offers"])
    assert all(o["partner_delta_pct"] < -0.004 for o in r["closest"])

    checked.clear()
    one = find_offers(ctx, 1, [target], ev, frozenset({1}), checked=checked)
    assert set(checked) == {"1-for-1"} and all(len(o["give"]) == 1 for o in one["offers"])
    two = find_offers(ctx, 1, ctx.team_roster(3)[:2], ev, frozenset({2}))
    assert all(len(o["give"]) == 2 for o in two["offers"] + two["closest"])


def test_ask_finder_prices_what_you_shop(snapshot):
    from collections import Counter

    from fbb.trades import find_asks

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    shop = ctx.team_roster(1)[0]
    checked: Counter = Counter()
    r = find_asks(ctx, 1, [shop], 3, ev, checked=checked)
    assert r["partner_id"] == 3 and r["give"] == [shop]
    assert set(checked) <= {"1-for-1", "1-for-2", "1-for-3"}
    assert r["offers"] or r["closest"]
    gains = [o["my_delta_ros"] for o in r["offers"]]
    assert gains == sorted(gains, reverse=True)  # the most you can ask for first
    theirs = set(ctx.team_roster(3))
    assert all(o["give"] == [shop] and set(o["get"]) <= theirs and o["partner_delta_pct"] >= -0.004
               and o["my_delta_pct"] >= -0.004 for o in r["offers"])  # fair to both teams
    assert all(o["partner_delta_pct"] < -0.004 for o in r["closest"])

    one = find_asks(ctx, 1, [shop], 3, ev, frozenset({1}))
    assert all(len(o["get"]) == 1 for o in one["offers"] + one["closest"])
    every = find_asks(ctx, 1, [shop], None, ev)
    assert every["partner_id"] is None
    assert all(o["partner_id"] != 1 for o in every["offers"])
    assert max(Counter(o["partner_id"] for o in every["offers"]).values(), default=0) <= 3  # spread across teams


def test_ask_finder_rejects_bad_picks(snapshot):
    from fbb.trades import find_asks

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    cases = {
        "at least one": ([], 2),
        "up to 3": (ctx.team_roster(1)[:4], 2),
        "isn't on": ([ctx.team_roster(2)[0]], 2),
        "isn't in this league": (["no-such-player"], 2),
        "another team": ([ctx.team_roster(1)[0]], 1),
    }
    for msg, (players, partner) in cases.items():
        with pytest.raises(ValueError, match=msg):
            find_asks(ctx, 1, players, partner, ev)


def test_three_team_finder_rotates_one_player_each(snapshot):
    from collections import Counter

    from fbb.trades import find_three_team_trades

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    checked: Counter = Counter()
    rows = find_three_team_trades(ctx, 1, ev, candidates=40, checked=checked)
    assert 0 < checked["3-team"] <= 40
    gains = [r["my_delta_ros"] for r in rows]
    assert gains == sorted(gains, reverse=True) and all(g > 0 for g in gains)
    for r in rows:
        me, p, q = r["teams"]
        assert me == 1 and len({me, p, q}) == 3
        moves = {(m["from_team"], m["to_team"]) for m in r["moves"]}
        assert moves == {(1, p), (p, q), (q, 1)}  # you -> P -> Q -> you
        assert r["give"] == [next(m["player_id"] for m in r["moves"] if m["from_team"] == 1)]
        assert r["get"] == [next(m["player_id"] for m in r["moves"] if m["to_team"] == 1)]
        assert [s["team_id"] for s in r["partners"]] == [p, q]
        assert all(s["delta_pct"] >= -0.004 for s in r["partners"])
    assert max(Counter(frozenset(r["teams"][1:]) for r in rows).values(), default=0) <= 2

    with_3 = find_three_team_trades(ctx, 1, ev, partners=[3], candidates=20)
    assert all(3 in r["teams"] for r in with_3)
    with_3_4 = find_three_team_trades(ctx, 1, ev, partners=[3, 4], candidates=20)
    assert all(set(r["teams"]) == {1, 3, 4} for r in with_3_4)


def test_offer_finder_rejects_bad_picks(snapshot):
    from fbb.trades import find_offers

    ctx = AnalysisContext(snapshot, ProjectionEngine(snapshot, model=None).project_all())
    ev = TradeEvaluator(ctx)
    fa = snapshot.free_agents()[0].id
    cases = {
        "at least one": [],
        "up to 3": ctx.team_roster(2)[:4],
        "already on": [ctx.team_roster(1)[0]],
        "free agent": [fa],
        "one team": [ctx.team_roster(2)[0], ctx.team_roster(3)[0]],
        "isn't in this league": ["no-such-player"],
    }
    for msg, targets in cases.items():
        with pytest.raises(ValueError, match=msg):
            find_offers(ctx, 1, targets, ev)
