"""AnalysisContext: everything the team-level models need, precomputed once per snapshot.

Holds player projections, per-day play probabilities over the rest of the
season, lineup-slot masks and per-game values, and exposes
``project_window`` -- the day-by-day optimal-lineup simulation that every
team-level feature (rankings, suggestions, trades) is built on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .lineup import SlotModel, solve_day
from .models import LeagueSnapshot
from .projections import PlayerProjection, ProjectionEngine, opening_factor, return_factor
from .schedule import game_state, period_for_date, period_to_matchup, remaining_days
from .valuation import CategoryValuer

CATEGORY_STATS = ["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FGM", "FGA", "FTM", "FTA", "3PA", "DD", "TD",
                  "OREB", "DREB", "PF"]
IR_STATUSES = ("OUT", "INJURY_RESERVE")  # statuses ESPN lets a team put in an IR slot

# Uncertainty beyond each game's own spread, for points leagues. A player's level over one matchup week
# moves together across his games (role, form, and the projection's own error), and a whole roster can
# run hot or cold together in a week. Without them weekly win odds were overconfident and 80% ranges held
# 70% of the time; with them, 80% (scripts/backtest_sims.py: 192 synthetic leagues, 2023-24 to 2025-26).
PLAYER_WEEK_CV = 0.10
TEAM_WEEK_CV = 0.06
# Except early in a season, when rosters don't run hot or cold together: with the full swing from the opener,
# 80% ranges held 90% of week-1 scores and 86% of week 2's, and win odds in weeks 1-3 were too timid
# (calibration slope 1.33). So the roster-wide swing grows in: TEAM_WEEK_CV x (1 - exp(-g / TEAM_WEEK_RAMP_GAMES)),
# g = the league's games per team before the week, nearly full by week 8. Ranges then held 83% and 79%, the
# slope was 1.15 and weekly log loss 0.0009 lower; ramps of 4 and 12 games did worse (96 local synthetic leagues).
TEAM_WEEK_RAMP_GAMES = 8.0

# A player who isn't hurt rarely misses his next game or two: the injuries that would keep him out are mostly
# on the report already. Games further off carry more of the season's injury risk. So a healthy player's
# chance of missing his next game is MISS_NEXT_FLOOR + MISS_NEXT_GAME x his season-level rate (1 - availability),
# and climbs back to the season rate as exp(-j / MISS_RAMP_GAMES) fades, j = his team's games from now.
# Fitted on 2023-24 to 2025-26 game logs (scripts/backtest_sims.py): healthy rotation players missed 8% of
# their next two games against 24% a season. Not after an injury: players back from one miss more than the
# season rate, not less.
MISS_NEXT_GAME = 0.30
MISS_NEXT_FLOOR = 0.0
MISS_RAMP_GAMES = 9.0
# Healthier still at the season's start, with the injury report fresh from camp: healthy players missed their
# team's 2nd to 6th games 15-25% less often than the ramp says, and in 2026-27 all 90 rostered players ESPN listed
# as active before their team's opener played it (the ramp alone expected 6.9 to sit, this 2.8). So in his
# team's n-th game of the season (0 = the opener) a healthy player's chance of missing is scaled by
# 1 - SEASON_START_MISS_CUT x exp(-n / SEASON_START_GAMES).
# Fitted on 2023-24 to 2025-26 game logs; without it, synthetic leagues started 3% too few player-games in week 1.
SEASON_START_MISS_CUT = 0.6
SEASON_START_GAMES = 2.0


@dataclass
class WindowResult:
    mean: float = 0.0
    var: float = 0.0
    by_mp: dict[int, list[float]] = field(default_factory=dict)  # mp -> [mean, var]
    starts: dict[str, float] = field(default_factory=dict)  # expected games started (incl. fill-ins)
    points: dict[str, float] = field(default_factory=dict)  # expected value contributed
    games: dict[str, float] = field(default_factory=dict)  # expected games played (started or not)
    stats: dict[str, float] = field(default_factory=dict)  # expected counting stats from started games
    stats_by_mp: dict[int, dict[str, float]] = field(default_factory=dict)
    stat_var_by_mp: dict[int, dict[str, float]] = field(default_factory=dict)
    empty_slot_games: float = 0.0
    bench_games: float = 0.0
    days: list[dict] = field(default_factory=list)
    cuts: dict[str, tuple[int, str | None]] = field(default_factory=dict)  # roster_cuts: dropped -> (day, who returned)


class AnalysisContext:
    def __init__(self, snapshot: LeagueSnapshot, projections: dict[str, PlayerProjection] | None = None,
                 model=None):
        self.s = snapshot
        self.proj = projections if projections is not None else ProjectionEngine(snapshot, model).project_all()
        self.scoring = snapshot.settings.scoring
        self.is_points = self.scoring.kind == "points"
        self.slots = SlotModel(snapshot.settings.roster_slots)
        self.days = remaining_days(snapshot)
        self.reg_days = remaining_days(snapshot, through_playoffs=False)
        self.day_to_mp = period_to_matchup(snapshot)
        self.team_days: dict[str, set[int]] = {}
        for p, teams in snapshot.schedule.games.items():
            if p < snapshot.current_period:
                continue
            for t in teams:
                self.team_days.setdefault(t, set()).add(p)
        self.cat_valuer = None if self.is_points else CategoryValuer(self)
        self.value_pg: dict[str, float] = {}
        self.mask: dict[str, int] = {}
        for pid, pr in self.proj.items():
            p = snapshot.players[pid]
            self.mask[pid] = self.slots.mask_for(p.eligible_slots, p.positions)
            self.value_pg[pid] = pr.fp_pg if self.is_points else self.cat_valuer.value_pg(pid)
        self._pplay: dict[str, dict[int, float]] = {}
        self._form: dict[str, dict[int, float]] = {}
        self._team_played: dict[str, int] = {}
        self._league_gp: float | None = None
        self._rostered = {e.player_id for t in snapshot.teams for e in t.roster}
        self._first_day: dict[str, int] = {}

    # -- availability ----------------------------------------------------------

    def p_play(self, pid: str) -> dict[int, float]:
        """period -> probability the player plays that day (0 when his team is idle)."""
        if pid in self._pplay:
            return self._pplay[pid]
        p = self.s.players[pid]
        pr = self.proj[pid]
        days = sorted(self.team_days.get(p.pro_team, ()))
        out: dict[int, float] = {}
        miss = 1 - pr.availability
        near = min(miss, MISS_NEXT_FLOOR + MISS_NEXT_GAME * miss)
        g_out = pr.games_out
        full_out = int(math.floor(g_out))
        frac = g_out - full_out
        team_gp = self.team_games_played(p.pro_team)
        for k, d in enumerate(days):
            if k < full_out:
                out[d] = 0.0
                continue
            if g_out == 0:
                fresh = 1 - SEASON_START_MISS_CUT * math.exp(-(team_gp + k) / SEASON_START_GAMES)
                avail = 1 - (miss - (miss - near) * math.exp(-k / MISS_RAMP_GAMES)) * fresh
            else:
                avail = 1 - miss
            out[d] = avail * (1 - frac) if k == full_out and frac > 0 else avail
        # Once today's game has started, what he has scored is already in the matchup totals, so only the
        # part of the game he hasn't played yet is left, and nothing once it's over: a player who sat or played
        # less than usual doesn't get the rest of a finished game.
        today = self.s.current_period
        if out.get(today):
            if self.today_state(p.pro_team) == "final":
                out[today] = 0.0
            else:
                played = sum(g.minutes for g in p.recent_games
                             if g.season == self.s.settings.season and g.period == today)
                if played:
                    out[today] *= max(0.0, 1 - played / max(pr.mpg, 1.0))
        self._pplay[pid] = out
        return out

    def form(self, pid: str) -> dict[int, float]:
        """period -> his expected output that day relative to his projection: below 1 in his first games of
        the season and his first games back from an absence (the opening and return dips in fbb/projections.py),
        counting from the games he has played and after those he's expected to miss. Days not listed are 1."""
        if pid in self._form:
            return self._form[pid]
        pr = self.proj[pid]
        days = sorted(self.team_days.get(self.s.players[pid].pro_team, ()))
        full_out = int(math.floor(pr.games_out))
        # The absence he comes back from: the one he's in (games missed so far and still to miss), else his last.
        away = pr.games_missed + full_out
        missed, back = (away, 0) if away else (pr.back_from, pr.games_back)
        out: dict[int, float] = {}
        for k, d in enumerate(days[full_out:]):
            f = opening_factor(pr.games_played + k + 1) * return_factor(back + k + 1, missed)
            if f > 0.999:
                break
            out[d] = f
        self._form[pid] = out
        return out

    def team_games_played(self, team: str) -> int:
        """A pro team's games this season before today."""
        if team not in self._team_played:
            self._team_played[team] = sum(1 for p, teams in self.s.schedule.games.items()
                                          if p < self.s.current_period and team in teams)
        return self._team_played[team]

    def team_week_cv(self) -> float:
        """The roster-wide week swing (TEAM_WEEK_CV), smaller in the season's first weeks."""
        if TEAM_WEEK_RAMP_GAMES <= 0:
            return TEAM_WEEK_CV
        if self._league_gp is None:  # the league's games per team before today
            games = self.s.schedule.games
            teams = len({t for ts in games.values() for t in ts}) or 30
            self._league_gp = sum(len(ts) for p, ts in games.items() if p < self.s.current_period) / teams
        return TEAM_WEEK_CV * (1 - math.exp(-self._league_gp / TEAM_WEEK_RAMP_GAMES))

    def today_state(self, team: str) -> str | None:
        """"upcoming", "live" or "final" for an NBA team's game today, as of the snapshot (None: can't tell)."""
        return game_state(self.s.schedule, self.s.current_period, team, self.s.fetched_at)

    def first_day(self, pid: str) -> int:
        """First day a player can play for a fantasy team: today if he's on one. A pickup waits for his waiver
        claim to clear (when ESPN says it processes, else the league's whole waiver period), and can't join a
        game that has already tipped off."""
        if pid in self._first_day:
            return self._first_day[pid]
        today = self.s.current_period
        p = self.s.players[pid]
        day = today
        if pid not in self._rostered:
            if p.on_waivers:
                clears = period_for_date(self.s.schedule, p.waiver_clears.date()) if p.waiver_clears else None
                day = max(today, clears if clears is not None else today + math.ceil(self.s.settings.waiver_hours / 24))
            if day == today and (self.today_state(p.pro_team) in ("live", "final") or any(
                    g.period == today and g.season == self.s.settings.season for g in p.recent_games)):
                day = today + 1
        self._first_day[pid] = day
        return day

    # -- roster limits ---------------------------------------------------------------

    def roster_cuts(self, team_id: int, roster: list[str]) -> dict[str, tuple[int, str | None]]:
        """Players a team has to drop when an injured player comes back: player -> (first day gone, who came back).

        A team holds its active slots plus one IR slot per injured player it can stash there (OUT or IR, or
        already in its IR slot, where ESPN leaves him whatever his status), and only until his first game back.
        From that day it's a player over the limit, so its weakest player from then on goes. Without this a
        returning IR player and everyone else played all season: a phantom extra player, worth 35-50 points a
        week in a real league, and a trade for an injured player looked 50 points a week better than it was.
        """
        slots = self.s.settings.roster_slots
        active = sum(n for code, n in slots.items() if code != "IR")
        ir = slots.get("IR", 0)
        roster = [p for p in dict.fromkeys(roster) if p in self.proj]
        if len(roster) <= active:
            return {}
        on_ir = {e.player_id for e in self.s.team(team_id).roster if e.slot == "IR"}
        back: dict[str, float] = {}  # IR-eligible player -> first day he can play again
        for pid in roster:
            if self.s.players[pid].injury_status in IR_STATUSES or pid in on_ir:
                back[pid] = min((d for d, v in self.p_play(pid).items() if v > 0), default=math.inf)

        def value_from(pid: str, day: int) -> float:
            return self.value_pg[pid] * sum(v for d, v in self.p_play(pid).items() if d >= day)

        cuts: dict[str, tuple[int, str | None]] = {}
        kept = list(roster)
        for day in sorted({self.s.current_period, *(b for b in back.values() if b != math.inf)}):
            stashed = sum(1 for p in kept if back.get(p, -1) > day)
            returning = next((p for p in kept if back.get(p) == day), None)  # may be the one dropped
            while len(kept) > active + min(ir, stashed):
                cands = [p for p in kept if back.get(p, -1) <= day] or kept
                worst = min(cands, key=lambda p: value_from(p, day))
                kept.remove(worst)
                cuts[worst] = (day, returning)
        return cuts

    def expected_games(self, pid: str, days: list[int] | None = None) -> float:
        pp = self.p_play(pid)
        if days is None:
            return sum(pp.values())
        return sum(pp.get(d, 0.0) for d in days)

    # -- the core simulation ------------------------------------------------------

    def project_window(self, roster: list[str], days: list[int] | None = None, collect_days: bool = False,
                       track_stats: bool | None = None, team_id: int | None = None,
                       leaves: dict[str, int] | None = None) -> WindowResult:
        """Expected fantasy output of ``roster`` over ``days`` with an optimal daily lineup.

        Starters are chosen by expected value. When a starter sits (injury,
        rest), the best playing bench player fills the vacancy; that depth value
        is credited in expectation. With ``team_id``, the roster keeps to that
        team's limits: when an injured player comes back with no room, its
        weakest player is dropped from then on (roster_cuts). Pickups play from
        their first_day. ``leaves`` are players who stay only until a day: the
        one dropped for a waiver claim plays until the claim clears.
        """
        days = self.days if days is None else days
        track_stats = (not self.is_points) if track_stats is None else track_stats
        res = WindowResult()
        leaves = leaves or {}
        roster = [pid for pid in roster if pid in self.proj]
        cuts = self.roster_cuts(team_id, [p for p in roster if p not in leaves]) if team_id is not None else {}
        res.cuts = cuts
        gone_on = {**{p: day for p, (day, _) in cuts.items()}, **leaves}
        pp = {}
        for pid in roster:
            start, gone = self.first_day(pid), gone_on.get(pid, math.inf)
            pp[pid] = {d: v for d, v in self.p_play(pid).items() if start <= d < gone}
        val = {pid: self.value_pg[pid] for pid in roster}
        fp = {pid: self.proj[pid].fp_pg for pid in roster}
        sd = {pid: self.proj[pid].fp_sd for pid in roster}
        form = {pid: self.form(pid) for pid in roster}
        n_slots = self.slots.n
        memo: dict[tuple, dict[int, int]] = {}
        index = {pid: i for i, pid in enumerate(roster)}
        week_pts: dict[tuple[str, int], float] = {}  # (player, matchup period) -> expected points

        for d in days:
            cands = []
            for pid in roster:
                prob = pp[pid].get(d, 0.0)
                if prob > 0:
                    cands.append((index[pid], val[pid] * form[pid].get(d, 1.0) * prob, self.mask[pid]))
            if not cands:
                if n_slots:
                    res.empty_slot_games += 0  # no games: not a lineup failure
                continue
            key = tuple((c[0], round(c[1], 3)) for c in cands)
            assigned = memo.get(key)
            if assigned is None:
                assigned = solve_day(cands, n_slots)
                memo[key] = assigned
            mp = self.day_to_mp.get(d, 0)
            bym = res.by_mp.setdefault(mp, [0.0, 0.0])
            open_exp = 0.0
            day_mean = 0.0
            starters = []
            bench = []
            for i, _, _ in cands:
                pid = roster[i]
                prob = pp[pid][d]
                res.games[pid] = res.games.get(pid, 0.0) + prob
                if i in assigned:
                    starters.append((pid, prob))
                    open_exp += 1 - prob
                else:
                    bench.append((pid, prob))
            res.empty_slot_games += n_slots - len(starters)
            # Fill-ins: bench players by value cover expected vacancies.
            bench.sort(key=lambda x: -val[x[0]])
            contributors = [(pid, prob) for pid, prob in starters]
            for pid, prob in bench:
                q = prob * min(1.0, open_exp)
                open_exp = max(0.0, open_exp - q)
                res.bench_games += prob - q
                if q > 0:
                    contributors.append((pid, q))
            for pid, q in contributors:
                f = form[pid].get(d, 1.0)
                m = q * fp[pid] * f
                v = q * (sd[pid] * f) ** 2 + q * (1 - q) * (fp[pid] * f) ** 2
                day_mean += m
                res.mean += m
                res.var += v
                bym[0] += m
                bym[1] += v
                week_pts[(pid, mp)] = week_pts.get((pid, mp), 0.0) + m
                res.starts[pid] = res.starts.get(pid, 0.0) + q
                res.points[pid] = res.points.get(pid, 0.0) + q * f * (fp[pid] if self.is_points else val[pid])
                if track_stats:
                    pg = self.proj[pid].per_game
                    smp = res.stats_by_mp.setdefault(mp, {})
                    svar = res.stat_var_by_mp.setdefault(mp, {})
                    for stat in CATEGORY_STATS:
                        x = pg.get(stat, 0.0) * f
                        if x:
                            smp[stat] = smp.get(stat, 0.0) + q * x
                            # Poisson-ish game variance plus the play/no-play Bernoulli term.
                            svar[stat] = svar.get(stat, 0.0) + q * x * 1.3 + q * (1 - q) * x * x
            if collect_days:
                res.days.append({
                    "period": d,
                    "matchup_period": mp,
                    "starters": [
                        {"player_id": roster[i], "slot": self.slots.slots[j]} for i, j in sorted(assigned.items(),
                                                                                                key=lambda x: x[1])
                    ],
                    "bench": [pid for pid, _ in bench],
                    "expected": day_mean,
                })
        # Week-level swings: a player's (and a roster's) level moves together across a week's games.
        for (pid, mp), m in week_pts.items():
            res.by_mp[mp][1] += (PLAYER_WEEK_CV * m) ** 2
            res.var += (PLAYER_WEEK_CV * m) ** 2
        team_cv = self.team_week_cv()
        for bym in res.by_mp.values():
            res.var += (team_cv * bym[0]) ** 2
            bym[1] += (team_cv * bym[0]) ** 2
        if not track_stats:
            # Totals of counting stats from started games (used for explanations).
            for pid, q in res.starts.items():
                for stat, x in self.proj[pid].per_game.items():
                    res.stats[stat] = res.stats.get(stat, 0.0) + q * x
        else:
            for smp in res.stats_by_mp.values():
                for stat, x in smp.items():
                    res.stats[stat] = res.stats.get(stat, 0.0) + x
        return res

    # -- convenience ---------------------------------------------------------------

    def matchup_days(self, mp: int, from_today: bool = True) -> list[int]:
        days = self.s.settings.matchup_periods.get(mp, [])
        if from_today:
            return [d for d in days if d >= self.s.current_period]
        return list(days)

    def remaining_mps(self, include_playoffs: bool = True) -> list[int]:
        reg = self.s.settings.regular_season_matchups
        mps = []
        for mp, days in sorted(self.s.settings.matchup_periods.items()):
            if not include_playoffs and mp > reg:
                continue
            if days and max(days) >= self.s.current_period:
                mps.append(mp)
        return mps

    def ros_points(self, pid: str) -> float:
        return self.proj[pid].fp_pg * self.expected_games(pid)

    def team_roster(self, team_id: int, include_ir: bool = True) -> list[str]:
        t = self.s.team(team_id)
        return [e.player_id for e in t.roster if e.player_id in self.proj and (include_ir or e.slot != "IR")]


def team_week_arrays(ctx: AnalysisContext, results: dict[int, WindowResult], mps: list[int]):
    """Stack per-team, per-matchup-period means/vars into arrays (teams x mps)."""
    ids = sorted(results)
    mean = np.zeros((len(ids), len(mps)))
    var = np.zeros((len(ids), len(mps)))
    for a, tid in enumerate(ids):
        for b, mp in enumerate(mps):
            m, v = results[tid].by_mp.get(mp, [0.0, 0.0])
            mean[a, b] = m
            var[a, b] = v
    return ids, mean, var
