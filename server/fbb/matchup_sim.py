"""Monte Carlo matchup simulator.

Simulates a head-to-head week box score by box score, rather than with the
Normal approximation used for season-long rankings:

  * Each player-game: does he play (availability / injury), a per-game role
    shock S ~ Gamma(mean 1) that scales every stat together (minutes, usage),
    then independent Poisson counts for each box-score event (2s and 3s made
    and missed, free throws made and missed, rebounds, assists, ...). Poisson
    thinning keeps makes and misses independent given S, so FG% emerges
    naturally. The shock variance is calibrated per player so the simulated
    fantasy-point SD matches his projected game-to-game SD:
        Var(FP) = Var_Poisson + mu^2 * Var(S)   =>   Var(S) = (sd^2 - Var_Poisson) / mu^2
  * Each day the lineup is set from expected values (as a manager would) and,
    because lineups lock at tip-off, a starter who sits is replaced by the best
    eligible bench player who does play.
  * Points leagues sum fantasy points; category leagues compare every category
    (percentages from simulated makes and attempts) and count categories won.

Totals already scored in the current matchup are added when the source
provides them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import context as _context
from .constants import LOWER_IS_BETTER, RATIO_STATS
from .context import AnalysisContext
from .lineup import solve_day
from .models import ScoringSettings

EVENTS = ["2PM", "2PX", "3PM", "3PX", "FTM", "FTX", "REB", "AST", "STL", "BLK", "TO", "DD", "TD", "PF"]
_E = {e: i for i, e in enumerate(EVENTS)}
DEFAULT_SIMS = 10_000
MAX_SIMS = 20_000
# Multiplies the calibrated role-shock variance. x1.4 helped less than the week-level shocks in fbb/context.py,
# which the simulator draws below (scripts/backtest_sims.py).
ROLE_SHOCK_SCALE = 1.0


def event_rates(per_game: dict[str, float]) -> np.ndarray:
    """Expected count of each event per game played."""
    fga, fgm = per_game.get("FGA", 0.0), per_game.get("FGM", 0.0)
    tpa, tpm = per_game.get("3PA", 0.0), per_game.get("3PM", 0.0)
    fta, ftm = per_game.get("FTA", 0.0), per_game.get("FTM", 0.0)
    two_a = max(fga - tpa, 0.0)
    two_m = min(max(fgm - tpm, 0.0), two_a)
    r = np.array([
        two_m, two_a - two_m, tpm, max(tpa - tpm, 0.0), ftm, max(fta - ftm, 0.0),
        per_game.get("REB", 0.0), per_game.get("AST", 0.0), per_game.get("STL", 0.0), per_game.get("BLK", 0.0),
        per_game.get("TO", 0.0), min(per_game.get("DD", 0.0), 0.95), min(per_game.get("TD", 0.0), 0.95),
        per_game.get("PF", 0.0),
    ])
    return np.maximum(r, 0.0)


def events_to_stats(ev: np.ndarray) -> dict[str, np.ndarray]:
    """Box-score stats from event counts (last axis = EVENTS)."""
    two_m, two_x = ev[..., _E["2PM"]], ev[..., _E["2PX"]]
    tpm, tpx = ev[..., _E["3PM"]], ev[..., _E["3PX"]]
    ftm, ftx = ev[..., _E["FTM"]], ev[..., _E["FTX"]]
    reb = ev[..., _E["REB"]]
    fgm = two_m + tpm
    out = {
        "FGM": fgm, "FGA": fgm + two_x + tpx, "3PM": tpm, "3PA": tpm + tpx, "FTM": ftm, "FTA": ftm + ftx,
        "PTS": 2 * two_m + 3 * tpm + ftm, "REB": reb, "OREB": 0.23 * reb, "DREB": 0.77 * reb,
        "AST": ev[..., _E["AST"]], "STL": ev[..., _E["STL"]], "BLK": ev[..., _E["BLK"]], "TO": ev[..., _E["TO"]],
        "DD": np.minimum(ev[..., _E["DD"]], 1), "TD": np.minimum(ev[..., _E["TD"]], 1), "PF": ev[..., _E["PF"]],
    }
    out["FGMI"] = out["FGA"] - out["FGM"]
    out["FTMI"] = out["FTA"] - out["FTM"]
    out["3PMI"] = out["3PA"] - out["3PM"]
    out["EFGM"] = out["FGM"] + 0.5 * out["3PM"]
    return out


def fp_weights(scoring: ScoringSettings) -> np.ndarray:
    """Fantasy points contributed by one occurrence of each event."""
    w = np.zeros(len(EVENTS))
    for i in range(len(EVENTS)):
        unit = np.zeros(len(EVENTS))
        unit[i] = 1.0
        stats = {k: float(v) for k, v in events_to_stats(unit).items()}
        w[i] = scoring.fantasy_points(stats)
    return w


@dataclass
class _Player:
    pid: str
    lam: np.ndarray
    shape: float  # Gamma shape of the role shock (mean 1, variance 1/shape)


@dataclass
class _Day:
    period: int
    cands: list[int]  # indices into the team's player list
    p: np.ndarray
    starters: list[tuple[int, int]]  # (candidate position, slot index)
    fill_order: dict[int, list[int]]  # slot index -> eligible bench candidate positions, best first


class MatchupSimulator:
    def __init__(self, ctx: AnalysisContext, seed: int = 2027):
        self.ctx = ctx
        self.rng = np.random.default_rng(seed)
        # Fantasy-point weights for the game-to-game spread calibration (category leagues use ESPN default points).
        self.fp_scoring = ctx.scoring if ctx.is_points else ScoringSettings()
        self.w_fp = fp_weights(self.fp_scoring)
        self._exclude: set[str] = set()

    # -- setup ---------------------------------------------------------------------

    def _player(self, pid: str) -> _Player:
        pr = self.ctx.proj[pid]
        lam = event_rates(pr.per_game)
        mu = float(lam @ self.w_fp)
        var_poisson = float(lam @ (self.w_fp ** 2))
        if mu > 2.0:
            v = (pr.fp_sd ** 2 - var_poisson) / mu ** 2
            v = float(np.clip(v, 0.01, 0.8))
        else:
            v = 0.15
        return _Player(pid=pid, lam=lam, shape=1.0 / (v * ROLE_SHOCK_SCALE))

    def _plan(self, roster: list[str], days: list[int], exclude: set[str],
              team_id: int | None = None) -> tuple[list[_Player], list[_Day]]:
        ctx = self.ctx
        players = [self._player(pid) for pid in roster if pid in ctx.proj]
        # As in project_window: a player dropped when an injured teammate returns stops playing that day.
        cuts = ctx.roster_cuts(team_id, roster) if team_id is not None else {}
        plans = []
        n_slots = ctx.slots.n
        for d in days:
            cands, probs, items = [], [], []
            for i, pl in enumerate(players):
                gone = pl.pid in exclude or d >= cuts.get(pl.pid, (float("inf"),))[0] or d < ctx.first_day(pl.pid)
                prob = 0.0 if gone else ctx.p_play(pl.pid).get(d, 0.0)
                if prob > 0:
                    items.append((len(cands), ctx.value_pg[pl.pid] * prob, ctx.mask[pl.pid]))
                    cands.append(i)
                    probs.append(prob)
            if not cands:
                continue
            assigned = solve_day(items, n_slots)
            starters = sorted(assigned.items(), key=lambda x: x[1])
            bench = sorted((k for k in range(len(cands)) if k not in assigned),
                           key=lambda k: -ctx.value_pg[players[cands[k]].pid])
            fill = {}
            for k, j in starters:
                fill[j] = [b for b in bench if ctx.mask[players[cands[b]].pid] >> j & 1]
            plans.append(_Day(period=d, cands=cands, p=np.array(probs), starters=starters, fill_order=fill))
        return players, plans

    # -- simulation -----------------------------------------------------------------

    def _simulate_team(self, players: list[_Player], plans: list[_Day], n: int):
        E = len(EVENTS)
        totals = np.zeros((n, E))
        contrib = np.zeros((n, len(players)))  # fantasy points by player
        starts = np.zeros(len(players))
        games = np.zeros(len(players))
        by_day = []
        # Week-level levels (mean 1) shared by each player's games, and by the whole roster.
        week = team = None
        if _context.PLAYER_WEEK_CV > 0:
            k_w = 1.0 / _context.PLAYER_WEEK_CV ** 2
            week = self.rng.gamma(k_w, 1.0 / k_w, size=(n, len(players)))
        if _context.TEAM_WEEK_CV > 0:
            k_t = 1.0 / _context.TEAM_WEEK_CV ** 2
            team = self.rng.gamma(k_t, 1.0 / k_t, size=n)
        for day in plans:
            k = len(day.cands)
            lam = np.stack([players[i].lam for i in day.cands])  # (k, E)
            shapes = np.array([players[i].shape for i in day.cands])
            plays = self.rng.random((n, k)) < day.p
            shock = self.rng.gamma(shapes, 1.0 / shapes, size=(n, k))
            if week is not None:
                shock *= week[:, day.cands]
            if team is not None:
                shock *= team[:, None]
            counts = self.rng.poisson(lam[None, :, :] * shock[:, :, None])  # (n, k, E)
            use = np.zeros((n, k), dtype=bool)
            for pos, _ in day.starters:
                use[:, pos] = plays[:, pos]
            for pos, j in day.starters:
                vacancy = ~plays[:, pos]
                for b in day.fill_order[j]:
                    if not vacancy.any():
                        break
                    take = vacancy & plays[:, b] & ~use[:, b]
                    use[:, b] |= take
                    vacancy &= ~take
            used_counts = counts * use[:, :, None]
            day_events = used_counts.sum(axis=1)
            totals += day_events
            fp = used_counts @ self.w_fp  # (n, k)
            for pos, i in enumerate(day.cands):
                contrib[:, i] += fp[:, pos]
                starts[i] += use[:, pos].mean()
                games[i] += plays[:, pos].mean()
            by_day.append({"period": day.period, "mean": float((day_events @ self.w_fp).mean()),
                           "games": float(plays.sum(axis=1).mean()), "starts": float(use.sum(axis=1).mean())})
        return totals, contrib, starts, games, by_day

    def _accrued(self, team_id: int, mp: int) -> tuple[float, dict[str, float]]:
        if mp != self.ctx.s.current_matchup_period:
            return 0.0, {}
        for m in self.ctx.s.matchups:
            if m.matchup_period != mp:
                continue
            if m.home_team_id == team_id:
                return m.home_score, dict(m.home_stats)
            if m.away_team_id == team_id:
                return m.away_score, dict(m.away_stats)
        return 0.0, {}

    def simulate(self, team_a: int, team_b: int, mp: int, exclude: set[str] | None = None,
                 n: int = DEFAULT_SIMS) -> dict:
        ctx = self.ctx
        if team_a == team_b:
            raise ValueError("Pick two different teams")
        days = ctx.matchup_days(mp)
        if not days:
            raise ValueError("That matchup period has already been played")
        n = int(np.clip(n, 500, MAX_SIMS))
        exclude = set(exclude or ())
        self._exclude = exclude
        sides = []
        for tid in (team_a, team_b):
            roster = ctx.team_roster(tid)
            players, plans = self._plan(roster, days, exclude, tid)
            totals, contrib, starts, games, by_day = self._simulate_team(players, plans, n)
            acc_pts, acc_stats = self._accrued(tid, mp)
            sides.append({"team_id": tid, "players": players, "totals": totals, "contrib": contrib,
                          "starts": starts, "games": games, "by_day": by_day, "acc_pts": acc_pts,
                          "acc_stats": acc_stats})

        if ctx.is_points:
            scores = [s["totals"] @ self.w_fp + s["acc_pts"] for s in sides]
            a_win = scores[0] > scores[1]
            tie = scores[0] == scores[1]
            cats = None
        else:
            a_win, tie, cats = self._categories(sides)
            scores = [s["totals"] @ self.w_fp for s in sides]  # production in FP terms, for display only
        b_win = ~a_win & ~tie
        result = {
            "matchup_period": mp,
            "is_current": mp == ctx.s.current_matchup_period,
            "is_playoff": mp > ctx.s.settings.regular_season_matchups,
            "days_simulated": len(days),
            "start": _iso(ctx, days[0]),
            "end": _iso(ctx, days[-1]),
            "sims": n,
            "scoring": "points" if ctx.is_points else "categories",
            "tie_prob": float(tie.mean()),
            "excluded": sorted(exclude),
            "teams": [],
        }
        lo = float(min(np.percentile(s, 0.5) for s in scores))
        hi = float(max(np.percentile(s, 99.5) for s in scores))
        edges = np.linspace(lo, hi, 33)
        result["bins"] = [float(x) for x in edges]
        for side, score, win in zip(sides, scores, (a_win, b_win)):
            result["teams"].append(self._side_summary(side, score, win, edges, days))
        margin = scores[0] - scores[1]
        m_lo, m_hi = np.percentile(margin, [0.5, 99.5])
        m_edges = np.linspace(min(m_lo, -1.0), max(m_hi, 1.0), 33)
        result["margin"] = {
            "mean": float(margin.mean()), "p10": float(np.percentile(margin, 10)), "p90": float(np.percentile(margin, 90)),
            "bins": [float(x) for x in m_edges],
            "hist": np.histogram(np.clip(margin, m_edges[0], m_edges[-1]), m_edges)[0].tolist(),
        }
        if cats is not None:
            result["categories"] = cats
        return result

    def _categories(self, sides):
        cats = self.ctx.scoring.categories
        stats = [events_to_stats(s["totals"]) for s in sides]
        won = [np.zeros(len(s["totals"])) for s in sides]
        rows = []
        for c in cats:
            vals = []
            for st, side in zip(stats, sides):
                acc = side["acc_stats"]
                if c in RATIO_STATS:
                    num, den = RATIO_STATS[c]
                    made = st[num] + acc.get(num, 0.0) if num != "EFGM" else st["EFGM"]
                    att = st[den] + acc.get(den, 0.0)
                    vals.append(np.where(att > 0, made / np.maximum(att, 1e-9), 0.0))
                else:
                    vals.append(st.get(c, np.zeros(len(st["PTS"]))) + acc.get(c, 0.0))
            a, b = vals
            if c in LOWER_IS_BETTER:
                a_c, b_c = a < b, b < a
            else:
                a_c, b_c = a > b, b > a
            won[0] += a_c + 0.5 * (a == b)
            won[1] += b_c + 0.5 * (a == b)
            rows.append({"category": c, "a_mean": float(a.mean()), "b_mean": float(b.mean()),
                         "a_win": float(a_c.mean()), "b_win": float(b_c.mean()), "tie": float((a == b).mean())})
        a_win = won[0] > won[1]
        tie = won[0] == won[1]
        dist = np.bincount(np.round(won[0]).astype(int), minlength=len(cats) + 1)[: len(cats) + 1]
        return a_win, tie, {"rows": rows, "a_cats_won_dist": (dist / dist.sum()).tolist(),
                            "a_cats_mean": float(won[0].mean()), "b_cats_mean": float(won[1].mean())}

    def _side_summary(self, side, score, win, edges, days) -> dict:
        ctx = self.ctx
        t = ctx.s.team(side["team_id"])
        q = np.percentile(score, [10, 25, 50, 75, 90])
        players = []
        for i, pl in enumerate(side["players"]):
            c = side["contrib"][:, i]
            pr = ctx.proj[pl.pid]
            swing = 0.0
            if c.std() > 1e-9:
                hi = c >= np.percentile(c, 75)
                lo = c <= np.percentile(c, 25)
                if 0 < hi.mean() < 1 and 0 < lo.mean() < 1:
                    swing = float(win[hi].mean() - win[lo].mean())
            players.append({
                "player_id": pl.pid, "name": pr.name, "pro_team": pr.pro_team, "positions": pr.positions,
                "injury_status": ctx.s.players[pl.pid].injury_status, "excluded": pl.pid in self._exclude,
                "games": float(side["games"][i]), "starts": float(side["starts"][i]),
                "mean": float(c.mean()), "p10": float(np.percentile(c, 10)), "p90": float(np.percentile(c, 90)),
                "swing": swing,
            })
        players.sort(key=lambda r: -r["mean"])
        by_day = []
        for d in side["by_day"]:
            dt = ctx.s.schedule.period_dates.get(d["period"])
            by_day.append({**d, "date": dt.isoformat() if dt else None})
        return {
            "team_id": t.id, "name": t.name, "abbrev": t.abbrev, "owner": t.owner_name,
            "win_prob": float(win.mean()), "accrued": side["acc_pts"] if ctx.is_points else None,
            "mean": float(score.mean()), "sd": float(score.std()),
            "p10": float(q[0]), "p25": float(q[1]), "median": float(q[2]), "p75": float(q[3]), "p90": float(q[4]),
            "hist": np.histogram(np.clip(score, edges[0], edges[-1]), edges)[0].tolist(),  # tails in edge bins
            "games": float(sum(d["games"] for d in side["by_day"])),
            "starts": float(sum(d["starts"] for d in side["by_day"])),
            "players": players, "by_day": by_day,
        }


def _iso(ctx: AnalysisContext, period: int) -> str | None:
    d = ctx.s.schedule.period_dates.get(period)
    return d.isoformat() if d else None


def week_options(ctx: AnalysisContext) -> list[dict]:
    """Matchup periods that still have days to play, for the simulator's week picker."""
    out = []
    for mp in ctx.remaining_mps():
        days = ctx.s.settings.matchup_periods.get(mp, [])
        out.append({
            "matchup_period": mp,
            "start": _iso(ctx, days[0]) if days else None,
            "end": _iso(ctx, days[-1]) if days else None,
            "is_current": mp == ctx.s.current_matchup_period,
            "is_playoff": mp > ctx.s.settings.regular_season_matchups,
        })
    return out
