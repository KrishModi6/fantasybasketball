"""Player projections: statistics first, news second.

Order of operations for every player (each step is recorded for the UI):

  1. Preseason sources
       SPS     2-season weighted, regressed, age-adjusted rates (baseline.py)
       ESPN    published preseason projection, when available
       ML      LightGBM residual model over the SPS baseline (ml/model.py)
     combined with convex weights learned in the backtest (scripts/train_model.py).
  2. In-season update. Gamma-Poisson shrinkage: the preseason rate acts as K
     minutes of prior evidence (K per stat, estimated from backtest error), and
     this season's games are added with exponential recency decay. Early in a
     season, last season's closing games are added too, behind this season's
     and at a reduced weight. From SEASON_ONLY_AFTER_GAMES games played, the
     prior and last season's games drop out: this season's games alone.
     A player's first games of a season run below his projection (the opening
     dip, deeper and longer for a rookie): their minutes count scaled back up,
     and the weekly windows (fbb/context.py) project his own first games below it.
  3. Bounded adjustments (news layer). Only structured signals are used and
     each is capped:
       injury status / return date -> games missed (availability only)
       new team                    -> wider uncertainty, no rate change
       manual override             -> minutes x [0.85, 1.15]
     Total minutes adjustment is clamped to [-20%, +15%]. Free-text news is
     displayed, never parsed into numbers (except return-time phrases in an
     official injury note, which only set games missed).
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

from .baseline import (
    FALLBACK_LEAGUE_RATES,
    RATE_STATS,
    line_rates,
    league_rates,
    per_game_from_rates,
    rates_from_per_game,
    sps_projection,
)
from .ml.features import build_features
from .ml.model import ProjectionModel
from .models import GameLog, LeagueSnapshot, Player, ScoringSettings, SeasonLine
from .schedule import period_for_date
from .xfp import XfpModel, xfp_per_game

DEFAULT_ENSEMBLE = {"sps": 0.4, "espn": 0.4, "ml": 0.2}
DEFAULT_PRIOR_MINUTES = {
    "REB": 900, "AST": 900, "STL": 2500, "BLK": 1800, "TO": 1500, "FGM": 900, "FGMI": 900, "FTM": 1200,
    "FTMI": 2500, "3PM": 1200, "3PMI": 1200, "DD": 2000, "TD": 4000, "PF": 1200,
}
# In-season weighting. scripts/backtest_recency.py replays every game of 2024-25 and 2025-26 from
# out-of-sample preseason projections: these settings beat both faster fading (12/5-game half-lives
# were about 0.4% less accurate) and no recency weighting at all (1.0-1.4% less accurate).
MINUTES_PRIOR_GAMES = 8.0
RATE_HALF_LIFE_GAMES = 25.0
MINUTES_HALF_LIFE_GAMES = 10.0
PRIOR_STRENGTH_SCALE = 1.0  # multiplier on the learned per-stat prior strengths (the backtest tests 2.0)
# Last season's closing games. ESPN returns each player's last 20 team games, so for the first weeks of a
# season they reach back into the previous one. They are already inside last season's line in the
# preseason projection, so the last 10 count at a quarter of a current game's weight, as the oldest
# evidence. In the same backtest this cut the error 0.8% (1.1% over each player's first 0-3 games) and
# helped in both seasons; half weight or all 20 games did better in 2025-26 but worse in 2024-25.
CARRYOVER_GAMES = 10
CARRYOVER_RATE_WEIGHT = 0.25
CARRYOVER_MINUTES_WEIGHT = 0.25
# From this many games played this season, a projection is this season's games alone: the preseason prior
# (built from last season's stats) and last season's closing games stop counting.
SEASON_ONLY_AFTER_GAMES = 15
# A player's first games of a season fall short of his projection: in 2023-24 to 2025-26, players who played
# their team's opener scored 9% below it in game 1, 7% in games 2-3 and 3% in games 4-8, then level. About 3/5
# of it is minutes, the rest output per minute. His n-th game is projected at (1 - OPENING_DIP_MINUTES x e) x
# (1 - OPENING_DIP_RATE x e), e = exp(-(n - 1) / OPENING_GAMES), in the weekly windows (fbb/context.py). The
# update counts his first games' minutes scaled back up by the same dip; their output per minute counts as it
# happened, which already takes part of the dip into the projection for his next games, hence a short dip.
# Tuned in scripts/backtest_sims.py's weekly projections: opening-week per-game projections had run 6-11% above
# what players scored, and this setting left rest-of-season error (scripts/backtest_recency.py) no worse in
# any season. Scaling per-minute output up too made it worse once the preseason prior drops out.
OPENING_DIP_MINUTES = 0.07
OPENING_DIP_RATE = 0.05
OPENING_GAMES = 2.0
# His first games back from an absence fall short too, by more the longer he was out: in 2023-24 to 2025-26, his
# first game back after missing one team game was 0.96 of his projection, 0.89 after three or four, 0.85 after
# seven to nine, 0.83 after 10-19 and 0.75 after 20-29, which also lasted longer (0.73 over three games).
# Nearly all of it is minutes. His j-th game back after missing L team games is projected at
# 1 - RETURN_DIP x ln(1 + L) x exp(-(j - 1) x RETURN_MISSED_PER_GAME / L): it fades over a game for every 8 he
# missed. It multiplies the opening dip, which is the same thing after the offseason: a player whose season
# starts late has both: his games 1-5 were 0.86 of his projection, now projected at 0.85 (game 1: 0.67, 0.74
# projected; the opening dip alone said 0.94 and 0.88). The update counts
# those games' minutes scaled back up. Fitted on mid-season returns in scripts/backtest_sims.py's weekly inputs.
RETURN_DIP = 0.07
RETURN_MISSED_PER_GAME = 8.0
# A player with no NBA minutes in the last two seasons (a rookie, mostly) has a deeper, longer dip of his own
# on top: his n-th game is also x (1 - ROOKIE_DIP_MINUTES x r) minutes and (1 - ROOKIE_DIP_RATE x r) output per
# minute, r = exp(-(n - 1) / ROOKIE_GAMES). In 2023-24 to 2025-26 rookies' first games ran about half their
# preseason minutes and 43% of its fantasy points, and they kept climbing for weeks, so the update read their
# early games as a smaller role than they grew into: after 5-20 games their rest-of-season FP/G ran 7-15% above
# projection. The update counts a rookie's early minutes scaled back up by his dip (output per minute as it
# happened, like everyone's). In the weekly windows the minutes part is scaled by how far his minutes so far
# fall short of his preseason projection (PlayerProjection.rookie_minutes_dip): a rookie already playing a full
# role has no minutes left to climb, and with the whole dip rookies who played 28+ minutes in their first five
# games were projected at 0.85 of their next ten (0.94 scaled). Chosen on rest-of-season FP/G after 0-20 games,
# for every rookie and for those projected for 20+ minutes (docs/METHODOLOGY.md 1.5.2): rookies' error
# 5.24 -> 5.00, 4.31 -> 4.01, 4.62 -> 4.39 FP/G, and their next four games projected at 1.03-1.08 of what they
# scored after 0-12 games (1.05-1.65 without it).
ROOKIE_DIP_MINUTES = 0.30
ROOKIE_DIP_RATE = 0.20
ROOKIE_GAMES = 8.0
POPULATION_CV = 0.36  # game-to-game coefficient of variation of fantasy points (prior)

INJURY_DEFAULT_GAMES = {
    "OUT": 4.0, "INJURY_RESERVE": 12.0, "SUSPENSION": 3.0,
    "DOUBTFUL": 0.75, "QUESTIONABLE": 0.4, "DAY_TO_DAY": 0.5,
}
MINUTES_ADJ_FLOOR, MINUTES_ADJ_CAP = -0.20, 0.15
MANUAL_LIMIT = 0.15


@dataclass
class SourceEstimate:
    source: str
    weight: float
    mpg: float
    availability: float | None
    fp_pg: float
    per_game: dict[str, float]


@dataclass
class Adjustment:
    kind: str
    label: str
    detail: str
    minutes_pct: float = 0.0
    games_out: float = 0.0


@dataclass
class PlayerProjection:
    player_id: str
    name: str
    pro_team: str
    positions: list[str]
    mpg: float
    availability: float
    games_out: float
    per_game: dict[str, float]
    fp_pg: float
    fp_sd: float
    preseason_fp_pg: float
    season_fp_pg: float | None
    recent_fp_pg: float | None
    games_played: int
    # This season's absences, for the dip in his first games back: team games he has missed in a row up to now (all
    # of his team's games so far if he hasn't played yet), else the absence he last came back from and his games since.
    games_missed: int = 0
    back_from: int = 0
    games_back: int = 0
    sources: list[SourceEstimate] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    ml_explanation: list[dict] = field(default_factory=list)
    in_season_weight: float = 0.0
    carryover_games: int = 0  # last season's closing games in the update
    carryover_weight: float = 0.0
    # Season to date per NBA.com: fantasy points per game as they happened and as his opportunities were
    # worth (xFP). The gap is luck, which the projection already discounts.
    tracked_fp_pg: float | None = None
    xfp_pg: float | None = None
    flags: list[str] = field(default_factory=list)
    # A rookie's minutes dip still ahead of him in the weekly windows: 1 before his first game, 0 once his
    # minutes so far reach his preseason projection.
    rookie_minutes_dip: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _decay(n_back: int, half_life: float) -> float:
    return 0.5 ** (n_back / half_life)


def opening_factors(n: float, rookie: bool = False, rookie_minutes: float = 1.0) -> tuple[float, float]:
    """Minutes and per-minute output in a player's n-th game of the season (1 = his first), relative to
    his settled level. A rookie (no NBA minutes in the last two seasons) has his own dip on top, its
    minutes part scaled by ``rookie_minutes``."""
    n = max(n, 1.0)
    e = math.exp(-(n - 1.0) / OPENING_GAMES)
    m, r = 1.0 - OPENING_DIP_MINUTES * e, 1.0 - OPENING_DIP_RATE * e
    if rookie:
        e = math.exp(-(n - 1.0) / ROOKIE_GAMES)
        m, r = m * (1.0 - ROOKIE_DIP_MINUTES * rookie_minutes * e), r * (1.0 - ROOKIE_DIP_RATE * e)
    return m, r


def opening_factor(n: float, rookie: bool = False, rookie_minutes: float = 1.0) -> float:
    """Fantasy output in a player's n-th game of the season relative to his settled level."""
    m, r = opening_factors(n, rookie, rookie_minutes)
    return m * r


def return_factor(j: float, missed: float) -> float:
    """Minutes (and fantasy output) in a player's j-th game back (1 = his first) after missing ``missed`` straight
    team games, relative to his settled level."""
    if missed <= 0:
        return 1.0
    return 1.0 - RETURN_DIP * math.log1p(missed) * math.exp(-(max(j, 1.0) - 1.0) * RETURN_MISSED_PER_GAME / missed)


def _without(line: SeasonLine, games: list[GameLog]) -> SeasonLine:
    """A season line minus some of its games."""
    stats = dict(line.stats)
    for g in games:
        for k, v in g.stats.items():
            if k in stats:
                stats[k] = max(0.0, stats[k] - v)
    return line.model_copy(update={"gp": max(0.0, line.gp - len(games)), "stats": stats,
                                   "minutes": max(0.0, line.minutes - sum(g.minutes for g in games))})


_DURATION = re.compile(r"(\d+)\s*(?:-|to|-to-)?\s*(\d+)?\s*(day|week|month)s?", re.I)
_SEASON_ENDING = re.compile(r"season[- ]ending|(?:remainder|rest) of the (?:\d{4}-\d{2} )?season|out for the season", re.I)


def games_from_injury_note(note: str | None, team_games_left: int) -> tuple[float, str] | None:
    """Very conservative parse of return-time phrases in an official injury note."""
    if not note:
        return None
    if _SEASON_ENDING.search(note):
        return float(team_games_left), "note says season-ending"
    m = _DURATION.search(note)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if hi > 24 or lo > hi:
        return None
    unit = m.group(3).lower()
    days = (lo + hi) / 2 * {"day": 1, "week": 7, "month": 30}[unit]
    games = days * 82 / 174  # NBA teams play ~82 games in ~174 days
    return min(games, float(team_games_left)), f"note mentions '{m.group(0)}'"


class ProjectionEngine:
    def __init__(self, snapshot: LeagueSnapshot, model: ProjectionModel | None = None):
        self.s = snapshot
        self.model = model if model is not None else ProjectionModel.load()
        self.scoring: ScoringSettings = snapshot.settings.scoring
        if self.scoring.kind == "categories":
            # Category leagues still get an FP/G column (ESPN default weights) for sorting.
            self.fp_scoring = ScoringSettings()
        else:
            self.fp_scoring = self.scoring
        self.season = snapshot.settings.season
        lines = [h for p in snapshot.players.values() for h in p.history if h.season < self.season]
        self.lg = league_rates(lines)
        self.ensemble = (self.model.ensemble if self.model else {}) or {}
        self.prior_minutes = (self.model.prior_minutes if self.model else {}) or DEFAULT_PRIOR_MINUTES
        self.fp_cv = (self.model.bundle.get("fp_game_cv") if self.model else None) or POPULATION_CV
        xfp = XfpModel.load()
        self.xfp_beta: dict[str, float] = xfp.rate_beta() if xfp else {}
        self._feats: dict[str, dict | None] = {}
        self._importance: dict[str, float] | None = None
        self._team_days: dict[str, list[int]] = {}  # each team's game days this season before today
        for d in sorted(snapshot.schedule.games):
            if d < snapshot.current_period:
                for t in snapshot.schedule.games[d]:
                    self._team_days.setdefault(t, []).append(d)

    # -- helpers -------------------------------------------------------------

    def _absences(self, p: Player, games: list[GameLog], gp: int) -> tuple[list[tuple[int, int]], int]:
        """For each of this season's logged games, (j, L): his j-th game back after missing L straight team games
        (L = 0: no absence since the season started). Plus the team games he has missed in a row up to now. A gap
        before his first logged game counts only when it is his first game of the season: ESPN's last 20 team
        games may start after older games of his."""
        out, prev, j, missed = [], 0, 0, 0
        for i, g in enumerate(games):
            days = self._team_days.get(g.team, [])
            gap = bisect_left(days, g.period) - bisect_right(days, prev)
            if i == 0 and gp > len(games):
                gap = 0
            if gap > 0:
                j, missed = 1, gap
            else:
                j += 1
            out.append((j, missed))
            prev = g.period
        team = self._team_days.get(p.pro_team, [])
        streak = len(team) - bisect_right(team, games[-1].period) if games else max(0, len(team) - gp)
        return out, streak

    def _prior_k(self, stat: str, weighted_minutes: float) -> float:
        """Prior strength in minutes; established players (more history) get a stronger prior."""
        k = self.prior_minutes.get(stat, 1000.0)
        if isinstance(k, dict):  # learned in training
            return (k["high"] if weighted_minutes >= k["split"] else k["low"]) * PRIOR_STRENGTH_SCALE
        return float(k)

    def _fp(self, per_game: dict[str, float]) -> float:
        return self.fp_scoring.fantasy_points(per_game)

    def _weights(self, target: str, available: list[str]) -> dict[str, float]:
        w = self.ensemble.get(target) or DEFAULT_ENSEMBLE
        sub = {k: w.get(k, 0.0) for k in available}
        tot = sum(sub.values())
        if tot <= 0:
            return {k: 1.0 / len(available) for k in available}
        return {k: v / tot for k, v in sub.items()}

    def _effective_weights(self, available: list[str]) -> dict[str, float]:
        """One weight per source for display: per-target blend weights averaged by each
        target's share of fantasy points (minutes scale every stat, so they count as the total)."""
        if self._importance is None:
            lg = self.lg[max(self.lg)] if self.lg else FALLBACK_LEAGUE_RATES
            base = self._fp(per_game_from_rates(lg, 30.0))
            imp = {}
            for k in RATE_STATS:
                bumped = dict(lg)
                bumped[k] = lg[k] * 1.01
                imp[k] = abs(self._fp(per_game_from_rates(bumped, 30.0)) - base)
            imp["mpg"] = sum(imp.values())
            self._importance = imp
        totals = {s: 0.0 for s in available}
        norm = 0.0
        for target, w_imp in self._importance.items():
            w = self._weights(target, available)
            for s_ in available:
                totals[s_] += w_imp * w.get(s_, 0.0)
            norm += w_imp
        return {k: v / norm for k, v in totals.items()} if norm else totals

    def _team_games_left(self, team: str) -> int:
        last = max(self.s.schedule.games) if self.s.schedule.games else self.s.current_period
        return self.s.schedule.team_games_between(team, self.s.current_period, last)

    # -- main ----------------------------------------------------------------

    def project_all(self) -> dict[str, PlayerProjection]:
        players = list(self.s.players.values())
        pre = self._preseason(players)
        # No team-minutes budget: teammates' recent games already carry an absence, and scaling healthy
        # players up toward 240 minutes overshot their real output by 7% (scripts/backtest_sims.py).
        out = {p.id: self._in_season(p, pre[p.id]) for p in players}
        overrides = {o.player_id: o for o in self.s.overrides}
        for p in players:
            self._finalize(p, out[p.id], overrides.get(p.id))
        return out

    def _preseason(self, players: list[Player]) -> dict[str, dict]:
        feats_rows, metas = [], []
        for p in players:
            hist = [h for h in p.history if h.season < self.season]
            espn = next((e for e in p.experts if e.season == self.season), None)
            years_pro = (self.season - p.debut_year) if p.debut_year else None
            feats, base = build_features(hist, self.season, p.age, years_pro, espn, p.adp, self.lg)
            feats_rows.append(feats)
            metas.append((p, base, espn))
        ml = self.model.predict(feats_rows) if self.model else {}
        result = {}
        for i, (p, base, espn) in enumerate(metas):
            src: dict[str, dict[str, float]] = {
                "sps": {"mpg": base.mpg, "avail": base.availability, **base.rates},
            }
            if espn and espn.minutes_per_game > 0:
                er = rates_from_per_game(espn.per_game, espn.minutes_per_game)
                src["espn"] = {"mpg": espn.minutes_per_game, "avail": min(1.0, espn.gp / 82), **er}
            if ml:
                src["ml"] = {
                    "mpg": float(np.clip(ml["mpg"][i], 3.0, 40.0)),
                    "avail": float(np.clip(ml["avail"][i], 0.05, 1.0)),
                    **{k: max(0.0, float(ml[k][i]) / 36) for k in RATE_STATS},
                }
            blended: dict[str, float] = {}
            for target in ["mpg", "avail"] + RATE_STATS:
                have = [s for s in src if target in src[s]]
                w = self._weights(target, have)
                blended[target] = sum(w[s] * src[s][target] for s in have)
            eff_w = self._effective_weights(list(src))
            sources = []
            for name, vals in src.items():
                pg = per_game_from_rates({k: vals.get(k, base.rates[k]) for k in RATE_STATS}, vals["mpg"])
                sources.append(SourceEstimate(
                    source={"sps": "Stat baseline (SPS)", "espn": "ESPN projection", "ml": "ML model"}[name],
                    weight=round(eff_w.get(name, 0.0), 3), mpg=vals["mpg"], availability=vals.get("avail"),
                    fp_pg=self._fp(pg), per_game=pg,
                ))
            result[p.id] = {"blend": blended, "sources": sources, "feats": feats_rows[i], "has_ml": bool(ml)}
        return result

    def _in_season(self, p: Player, pre: dict) -> PlayerProjection:
        blend = pre["blend"]
        prior_rates = {k: blend[k] for k in RATE_STATS}
        prior_mpg = blend["mpg"]

        # Today's games may still be on, and ESPN counts the minutes so far in both the game log and the
        # season totals, so a game at halftime would read as a quiet full game. They count from tomorrow.
        today = [g for g in p.recent_games if g.season == self.season and g.period >= self.s.current_period]
        done = [g for g in p.recent_games if g not in today]
        games = [g for g in done if g.season == self.season]
        carry = [g for g in done if g.season == self.season - 1][-CARRYOVER_GAMES:]
        cur = p.current if p.current and p.current.season == self.season else None
        if cur and today:
            cur = _without(cur, today)
        obs_tot = {k: 0.0 for k in RATE_STATS}
        obs_min = 0.0
        w_minutes_sum = 0.0
        mins_prior_w = 0.0
        seen = max(len(games), int(cur.gp) if cur else 0)
        back, games_missed = self._absences(p, games, seen)
        w_min = pre["feats"].get("w_min", 0.0)
        rookie = w_min == 0.0
        # Most recent game is index 0 in the decay. His first games of the season and first games back from an
        # absence count their minutes scaled up by their dips.
        for n_back, g in enumerate(reversed(games)):
            w = _decay(n_back, RATE_HALF_LIFE_GAMES)
            tot = line_rates_from_game(g.stats)
            for k in RATE_STATS:
                obs_tot[k] += w * tot.get(k, 0.0)
            obs_min += w * g.minutes
            wm = _decay(n_back, MINUTES_HALF_LIFE_GAMES)
            j, missed = back[len(games) - 1 - n_back]
            w_minutes_sum += wm * g.minutes / (opening_factors(seen - n_back, rookie)[0] * return_factor(j, missed))
            mins_prior_w += wm
        # Season-to-date beyond the logged games, as one older block: his first extra_gp games.
        if cur and cur.gp > len(games):
            extra_gp = cur.gp - len(games)
            extra_min = max(0.0, cur.minutes - sum(g.minutes for g in games))
            logged = {k: 0.0 for k in RATE_STATS}
            for g in games:
                for k, v in line_rates_from_game(g.stats).items():
                    logged[k] += v
            cur_tot = line_rates(cur)
            w = _decay(len(games) + extra_gp / 2, RATE_HALF_LIFE_GAMES)
            for k in RATE_STATS:
                obs_tot[k] += w * max(0.0, cur_tot.get(k, 0.0) - logged[k])
            obs_min += w * extra_min
            wm = _decay(len(games) + extra_gp / 2, MINUTES_HALF_LIFE_GAMES)
            f_min = float(np.mean([opening_factors(n, rookie)[0] for n in range(1, max(1, round(extra_gp)) + 1)]))
            w_minutes_sum += wm * extra_min / f_min
            mins_prior_w += wm * extra_gp
        # xFP: this season's shooting, passing, rebounding, steals and blocks move part of the way from what
        # happened toward what his opportunities were worth (fbb/xfp.py), by weights learned in
        # scripts/backtest_xfp.py. Attempts, minutes and everything else stay as they happened.
        season_min = obs_min
        xt = p.tracking if p.tracking and p.tracking.season == self.season and p.tracking.minutes > 0 else None
        if xt and season_min > 0:
            act, exp = line_rates_from_game(xt.actual), line_rates_from_game(xt.expected)
            for k, beta in self.xfp_beta.items():
                obs_tot[k] = max(0.0, obs_tot[k] + beta * (exp[k] - act[k]) / xt.minutes * season_min)
        # Last season's closing games continue the decay behind everything from this season, until he has
        # played SEASON_ONLY_AFTER_GAMES games this season.
        season_only = seen >= SEASON_ONLY_AFTER_GAMES and season_min > 0
        if season_only:
            carry = []
        for i, g in enumerate(reversed(carry)):
            w = CARRYOVER_RATE_WEIGHT * _decay(seen + i, RATE_HALF_LIFE_GAMES)
            for k, v in line_rates_from_game(g.stats).items():
                obs_tot[k] += w * v
            obs_min += w * g.minutes
            wm = CARRYOVER_MINUTES_WEIGHT * _decay(seen + i, MINUTES_HALF_LIFE_GAMES)
            w_minutes_sum += wm * g.minutes
            mins_prior_w += wm

        K = {k: 0.0 if season_only else self._prior_k(k, w_min) for k in RATE_STATS}
        rates = {k: (K[k] * prior_rates[k] + obs_tot[k]) / (K[k] + obs_min) for k in RATE_STATS}
        k_mpg = 0.0 if season_only else MINUTES_PRIOR_GAMES
        mpg = (k_mpg * prior_mpg + w_minutes_sum) / (k_mpg + mins_prior_w)
        k_avg = float(np.mean([K[k] for k in ("FGM", "REB", "AST")]))
        in_season_weight = season_min / (obs_min + k_avg) if obs_min else 0.0
        carryover_weight = (obs_min - season_min) / (obs_min + k_avg) if obs_min else 0.0

        per_game = per_game_from_rates(rates, mpg)
        pre_pg = per_game_from_rates(prior_rates, prior_mpg)
        season_fp = None
        if cur and cur.gp > 0:
            season_fp = self._fp({k: v / cur.gp for k, v in cur.stats.items()})
        # Last 10 games, reaching back into last season while this one is young.
        last10 = (carry + games)[-10:]
        recent_fp = float(np.mean([self._fp(g.stats) for g in last10])) if len(last10) >= 3 else None

        # Game-to-game volatility: shrink observed SD (this season's logs, plus last season's until the cutoff)
        # to the population CV.
        fp = self._fp(per_game)
        fps = [self._fp(g.stats) for g in (games if season_only else done)[-30:]]
        prior_var = (self.fp_cv * max(fp, 5.0)) ** 2
        if len(fps) >= 5:
            n = len(fps)
            sd = math.sqrt((np.var(fps, ddof=1) * (n - 1) + prior_var * 10) / (n - 1 + 10))
        else:
            sd = math.sqrt(prior_var)

        self._feats[p.id] = pre["feats"] if pre["has_ml"] else None
        tracked_fp, xfp_fp = xfp_per_game(xt, self.fp_scoring) if xt and xt.gp else (None, None)
        back_games, back_from = back[-1] if back and not games_missed else (0, 0)
        # A rookie already playing his projected minutes has no minutes dip left to come; one playing as
        # far below them as the dip goes still has all of it.
        rookie_minutes = 0.0
        if rookie:
            rookie_minutes = 1.0
            played = cur.minutes / cur.gp if cur and cur.gp else None
            if played is None and games:
                played = float(np.mean([g.minutes for g in games]))
            if played is not None and ROOKIE_DIP_MINUTES > 0:
                shortfall = 1.0 - played / max(prior_mpg, 1.0)
                rookie_minutes = min(1.0, max(0.0, shortfall / ROOKIE_DIP_MINUTES))

        return PlayerProjection(
            player_id=p.id, name=p.name, pro_team=p.pro_team, positions=p.positions,
            mpg=mpg, availability=blend["avail"], games_out=0.0, per_game=per_game,
            fp_pg=fp, fp_sd=sd, preseason_fp_pg=self._fp(pre_pg), season_fp_pg=season_fp,
            recent_fp_pg=recent_fp, games_played=int(cur.gp) if cur else 0,
            games_missed=games_missed, back_from=back_from, games_back=back_games if back_from else 0,
            sources=pre["sources"], in_season_weight=in_season_weight,
            carryover_games=len(carry) if CARRYOVER_RATE_WEIGHT or CARRYOVER_MINUTES_WEIGHT else 0,
            carryover_weight=carryover_weight, tracked_fp_pg=tracked_fp, xfp_pg=xfp_fp,
            flags=["rookie"] if rookie else [], rookie_minutes_dip=rookie_minutes,
        )

    def explain(self, pr: PlayerProjection) -> list[dict]:
        """SHAP attribution of the ML component, in fantasy points per game (computed on demand)."""
        feats = self._feats.get(pr.player_id)
        if feats is None or self.model is None:
            return []
        rates = {k: v / max(pr.mpg, 1e-6) for k, v in pr.per_game.items() if k in RATE_STATS}
        try:
            return self._explain_fp(self.model.contributions(feats), rates, pr.mpg)
        except Exception:  # noqa: BLE001 - explanations are best-effort
            return []

    def _explain_fp(self, contrib: dict[str, dict[str, float]], rates: dict[str, float], mpg: float) -> list[dict]:
        """Linearise SHAP contributions into fantasy points per game.

        FP/G = sum_s w_s * rate_s * mpg, so d(FP/G) ~= sum_s w_s * (mpg * d rate_s + rate_s * d mpg).
        Rate models are per-36, hence the /36.
        """
        groups: dict[str, float] = {}
        # Marginal FP per unit of each modelled per-minute rate, at the projected minutes.
        for stat in RATE_STATS:
            if stat not in contrib:
                continue
            bumped = dict(rates)
            bumped[stat] = rates[stat] + 1.0 / 36
            delta_fp = self._fp(per_game_from_rates(bumped, mpg)) - self._fp(per_game_from_rates(rates, mpg))
            for g, v in contrib[stat].items():
                groups[g] = groups.get(g, 0.0) + v * delta_fp
        if "mpg" in contrib:
            fp_per_min = self._fp(per_game_from_rates(rates, 1.0))
            for g, v in contrib["mpg"].items():
                groups[g] = groups.get(g, 0.0) + v * fp_per_min
        return sorted(({"factor": g, "fp_pg": round(v, 2)} for g, v in groups.items() if abs(v) >= 0.05),
                      key=lambda x: -abs(x["fp_pg"]))

    def _finalize(self, p: Player, pr: PlayerProjection, override) -> None:
        games_left = self._team_games_left(p.pro_team)
        # New team since last season: widen uncertainty only.
        last_team = p.history[-1].team if p.history else None
        recent_team = p.recent_games[-1].team if p.recent_games else None
        prev = recent_team or last_team
        if prev and p.pro_team not in ("FA", "") and prev not in ("", "FA") and prev != p.pro_team:
            pr.fp_sd *= 1.15
            pr.flags.append("new_team")
            pr.adjustments.append(Adjustment(
                kind="team_change", label=f"New team ({prev} → {p.pro_team})",
                detail="Role is less certain after a team change: game-to-game uncertainty widened 15%. "
                       "Rates are unchanged; his games with the new team update minutes as they come.",
            ))

        if override is not None and override.minutes_pct:
            pct = max(-MANUAL_LIMIT, min(MANUAL_LIMIT, override.minutes_pct))
            pr.adjustments.append(Adjustment(
                kind="manual", label="League override",
                detail=(override.note or "Manual minutes adjustment") + f" ({pct:+.0%}, capped at ±15%).",
                minutes_pct=pct,
            ))

        total_pct = sum(a.minutes_pct for a in pr.adjustments)
        total_pct = max(MINUTES_ADJ_FLOOR, min(MINUTES_ADJ_CAP, total_pct))
        if total_pct:
            pr.mpg *= 1.0 + total_pct
            rates = {k: v / max(pr.per_game.get("MIN", 1.0), 1e-6) for k, v in pr.per_game.items()
                     if k in RATE_STATS}
            pr.per_game = per_game_from_rates(rates, pr.mpg)
            pr.fp_sd *= 1.0 + total_pct

        # Injury: affects games played only.
        status = (p.injury_status or "ACTIVE").upper()
        games_out = 0.0
        if override is not None and override.out_games is not None:
            games_out = float(override.out_games)
            pr.adjustments.append(Adjustment(kind="injury", label="League override: games out",
                                              detail=f"Set to {games_out:.0f} games by a league member.",
                                              games_out=games_out))
        elif status in INJURY_DEFAULT_GAMES:
            games_out = INJURY_DEFAULT_GAMES[status]
            detail = f"ESPN status {status.replace('_', ' ').title()}: default {games_out:g} games missed."
            if p.expected_return:
                ret_p = period_for_date(self.s.schedule, p.expected_return)
                if ret_p is not None:
                    games_out = float(self.s.schedule.team_games_between(p.pro_team, self.s.current_period, ret_p - 1))
                    detail = f"Expected return {p.expected_return.isoformat()}: {games_out:.0f} team games."
            elif status in ("OUT", "INJURY_RESERVE"):
                parsed = games_from_injury_note(p.injury_note, games_left)
                if parsed and parsed[0] > games_out:
                    games_out, why = parsed
                    detail = f"Injury report {why}: ~{games_out:.0f} games missed (capped at games remaining)."
            pr.adjustments.append(Adjustment(kind="injury", label=f"Injury: {status.replace('_', ' ').title()}",
                                              detail=detail, games_out=games_out))
        pr.games_out = min(games_out, float(games_left))
        pr.fp_pg = self._fp(pr.per_game)


def line_rates_from_game(stats: dict[str, float]) -> dict[str, float]:
    from .models import SeasonLine

    return line_rates(SeasonLine(season=0, stats=stats))


def project_snapshot(snapshot: LeagueSnapshot, model: ProjectionModel | None = None) -> dict[str, PlayerProjection]:
    return ProjectionEngine(snapshot, model).project_all()


def as_of(snapshot: LeagueSnapshot) -> date | None:
    return snapshot.schedule.period_dates.get(snapshot.current_period)
