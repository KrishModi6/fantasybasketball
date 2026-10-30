"""Team strength: weekly score distributions, matchup odds, season simulation.

Points leagues: a team's weekly score is approximately Normal with the mean and
variance accumulated from every expected start in ``AnalysisContext.project_window``
(sum of many independent player-games -> CLT), so
    P(A beats B) = Phi((mu_A - mu_B) / sqrt(var_A + var_B)).

Category leagues: each category total is approximated as Normal (percentages
via the delta method), giving per-category win probabilities; the matchup
result is the Poisson-binomial count of categories won (ties split).

The headline "power score" is the expected all-play win rate over the
remaining regular-season weeks (each team vs. every other team, every week),
which removes schedule luck. Playoff and title odds come from a Monte Carlo
of the actual remaining schedule and the bracket.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from itertools import combinations

import numpy as np

from .constants import LOWER_IS_BETTER, RATIO_STATS
from .context import AnalysisContext, WindowResult


def normal_cdf(z: float) -> float:
    """Standard normal CDF. erfc keeps precision in the tails."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


N_SIMS = 5000


@dataclass
class TeamStrength:
    team_id: int
    name: str
    abbrev: str
    owner: str | None
    rank: int = 0
    power_score: float = 0.0  # record_weight * record_pct + (1 - record_weight) * projected_allplay
    projected_allplay: float = 0.0  # expected all-play win rate over the remaining regular-season weeks
    record_pct: float | None = None  # this season's win rate, ties as half (None before any game)
    record_weight: float = 0.0  # share of the regular season already played
    ppw: float = 0.0
    ppw_vs_avg: float = 0.0
    wins: int = 0
    losses: int = 0
    ties: int = 0
    proj_wins: float = 0.0  # expected final wins
    proj_losses: float = 0.0  # proj_wins + proj_losses + proj_ties == proj_games
    proj_ties: int = 0  # ties so far (the simulation plays the rest to a result)
    proj_games: int = 0  # regular-season games: fewer than the weeks when the team has byes
    byes: int = 0  # regular-season weeks without a game (an odd number of teams leaves one out each week)
    playoff_odds: float = 0.0
    title_odds: float = 0.0
    avg_seed: float = 0.0
    current: dict | None = None
    explanation: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def proj_record(self) -> tuple[int, int, int]:
        """The projected record in whole games: expected wins rounded, the rest losses (and the ties so far),
        so it adds up to the team's games. Rounding wins and losses each on their own could make it a game
        more."""
        w = math.floor(self.proj_wins + 0.5)
        return w, self.proj_games - self.proj_ties - w, self.proj_ties


class MatchupModel:
    """Win probabilities from per-team, per-matchup-period window results."""

    def __init__(self, ctx: AnalysisContext, results: dict[int, WindowResult]):
        self.ctx = ctx
        self.results = results
        # Points already scored in an undecided week: this week's so far, or the final score of a week that's
        # over but has no result in the league yet (which then decides it).
        self.partial: dict[tuple[int, int], float] = {}
        cur = ctx.s.current_matchup_period
        if ctx.is_points:
            for m in ctx.s.matchups:
                if m.matchup_period <= cur and m.winner == "UNDECIDED":
                    self.partial[(m.home_team_id, m.matchup_period)] = m.home_score
                    if m.away_team_id is not None:
                        self.partial[(m.away_team_id, m.matchup_period)] = m.away_score

    def dist(self, team_id: int, mp: int) -> tuple[float, float]:
        m, v = self.results[team_id].by_mp.get(mp, [0.0, 0.0])
        return m + self.partial.get((team_id, mp), 0.0), v

    def cat_probs(self, a: int, b: int, mp: int) -> dict[str, float]:
        cats = self.ctx.scoring.categories
        sa = self.results[a].stats_by_mp.get(mp, {})
        sb = self.results[b].stats_by_mp.get(mp, {})
        va = self.results[a].stat_var_by_mp.get(mp, {})
        vb = self.results[b].stat_var_by_mp.get(mp, {})
        out = {}
        for c in cats:
            if c in RATIO_STATS:
                num, den = RATIO_STATS[c]
                ma, aa = _made(sa, num), sa.get(den, 0.0)
                mb, ab = _made(sb, num), sb.get(den, 0.0)
                if aa <= 0 or ab <= 0:
                    out[c] = 0.5
                    continue
                pa, pb = ma / aa, mb / ab
                # Delta method: Var(M/A) ~ Var(M)/A^2 + p^2 Var(A)/A^2 - 2p Cov/A^2 (binomial makes given attempts)
                var_a = (aa * pa * (1 - pa) + 0.15 * va.get(den, aa) * pa * pa) / aa ** 2
                var_b = (ab * pb * (1 - pb) + 0.15 * vb.get(den, ab) * pb * pb) / ab ** 2
                z = (pa - pb) / math.sqrt(max(var_a + var_b, 1e-9))
            else:
                key = c
                z = (sa.get(key, 0.0) - sb.get(key, 0.0)) / math.sqrt(max(va.get(key, 0.0) + vb.get(key, 0.0), 1e-6))
                if c in LOWER_IS_BETTER:
                    z = -z
            out[c] = normal_cdf(z)
        return out

    def p_win(self, a: int, b: int, mp: int) -> float:
        if self.ctx.is_points:
            ma, va = self.dist(a, mp)
            mb, vb = self.dist(b, mp)
            return normal_cdf((ma - mb) / math.sqrt(max(va + vb, 1.0)))
        probs = list(self.cat_probs(a, b, mp).values())
        dist = np.zeros(len(probs) + 1)
        dist[0] = 1.0
        for p in probs:  # Poisson-binomial DP
            dist[1:] = dist[1:] * (1 - p) + dist[:-1] * p
            dist[0] *= 1 - p
        n = len(probs)
        wins = dist[np.arange(n + 1) > n / 2].sum()
        ties = dist[n // 2] if n % 2 == 0 else 0.0
        return float(wins + 0.5 * ties)


def _made(stats: dict[str, float], num: str) -> float:
    if num == "EFGM":
        return stats.get("FGM", 0.0) + 0.5 * stats.get("3PM", 0.0)
    return stats.get(num, 0.0)


def team_windows(ctx: AnalysisContext, rosters: dict[int, list[str]] | None = None) -> dict[int, WindowResult]:
    out = {}
    for t in ctx.s.teams:
        roster = rosters[t.id] if rosters and t.id in rosters else ctx.team_roster(t.id)
        out[t.id] = ctx.project_window(roster)
    return out


def remaining_schedule(ctx: AnalysisContext) -> list[tuple[int, int, int]]:
    """(mp, home, away) for the regular-season matchups not in the teams' records; round-robin if unknown.

    That's every undecided matchup from the current week on, and a week that's over with no result yet
    (ESPN between a week's end and its results; a demo league, which never has any). A file import's
    records can already count those weeks, so a past one is added only while both teams' records and
    games left fall short of their schedule.
    """
    reg = ctx.s.settings.regular_season_matchups
    cur = ctx.s.current_matchup_period
    scheduled = [m for m in ctx.s.matchups if m.away_team_id is not None and m.matchup_period <= reg]
    if scheduled:
        games = [(m.matchup_period, m.home_team_id, m.away_team_id) for m in scheduled
                 if m.matchup_period >= cur and m.winner == "UNDECIDED"]
        short = {t.id: -(t.wins + t.losses + t.ties) for t in ctx.s.teams}
        for m in scheduled:
            short[m.home_team_id] = short.get(m.home_team_id, 0) + 1
            short[m.away_team_id] = short.get(m.away_team_id, 0) + 1
        for _, a, b in games:
            short[a] -= 1
            short[b] -= 1
        past = [m for m in scheduled if m.matchup_period < cur and m.winner == "UNDECIDED"]
        for m in sorted(past, key=lambda m: -m.matchup_period):  # the latest week is the one still to post
            if short[m.home_team_id] > 0 and short[m.away_team_id] > 0:
                games.append((m.matchup_period, m.home_team_id, m.away_team_id))
                short[m.home_team_id] -= 1
                short[m.away_team_id] -= 1
        return games
    ids = [t.id for t in ctx.s.teams]
    rr = round_robin(ids)
    out = []
    for mp in range(cur, reg + 1):
        for a, b in rr[(mp - 1) % len(rr)]:
            out.append((mp, a, b))
    return out


def round_robin(ids: list[int]) -> list[list[tuple[int, int]]]:
    teams = list(ids)
    if len(teams) % 2:
        teams.append(-1)
    n = len(teams)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = teams[i], teams[n - 1 - i]
            if a != -1 and b != -1:
                pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


def simulate_season(ctx: AnalysisContext, mm: MatchupModel, n: int = N_SIMS, seed: int = 11,
                    ppw: dict[int, float] | None = None) -> dict[int, dict]:
    rng = np.random.default_rng(seed)
    ids = [t.id for t in ctx.s.teams]
    col = {tid: i for i, tid in enumerate(ids)}
    T = len(ids)
    won = np.zeros((n, T))  # wins in the games simulated
    left = {tid: 0 for tid in ids}
    pcache: dict[tuple, float] = {}
    for mp, a, b in remaining_schedule(ctx):
        key = (a, b, mp)
        if key not in pcache:
            pcache[key] = mm.p_win(a, b, mp)
        a_wins = rng.random(n) < pcache[key]
        won[:, col[a]] += a_wins
        won[:, col[b]] += ~a_wins
        left[a] += 1
        left[b] += 1
    wins = won + np.array([t.wins + 0.5 * t.ties for t in ctx.s.teams])  # standings: a tie is half a win

    # Seeding: wins, then a tiebreak that favours stronger teams (proxy for H2H/points).
    strength = np.array([ppw.get(tid, 0.0) if ppw else 0.0 for tid in ids])
    tiebreak = strength / (strength.std() + 1e-9) * 0.01 + rng.random((n, T)) * 0.02
    order = np.argsort(-(wins + tiebreak), axis=1)
    k = min(ctx.s.settings.playoff_teams, T)
    seeds = order[:, :k]  # (n, k) team columns by seed
    playoff_mps = sorted(mp for mp in ctx.s.settings.matchup_periods if mp > ctx.s.settings.regular_season_matchups)
    champion = _simulate_bracket(seeds, ids, mm, playoff_mps, rng)

    out = {}
    reg = ctx.s.settings.regular_season_matchups
    for t in ctx.s.teams:
        c = col[t.id]
        made = (seeds == c).any(axis=1)
        seed_pos = np.where(seeds == c, np.arange(1, k + 1), 0).sum(axis=1)
        games = t.wins + t.losses + t.ties + left[t.id]
        exp_wins = t.wins + float(won[:, c].mean())
        out[t.id] = {
            "proj_wins": exp_wins,
            "proj_losses": games - t.ties - exp_wins,
            "proj_ties": t.ties,
            "proj_games": games,
            "byes": max(0, reg - games),
            "playoff_odds": float(made.mean()),
            "title_odds": float((champion == c).mean()),
            "avg_seed": float(seed_pos[made].mean()) if made.any() else 0.0,
        }
    return out


def _bracket_order(k: int) -> list[int]:
    """Standard seeding order for a bracket of size k (power of two): 1 v k, ..."""
    order = [1]
    while len(order) < k:
        size = len(order) * 2
        order = [x for s in order for x in (s, size + 1 - s)]
    return order


def _simulate_bracket(seeds: np.ndarray, ids: list[int], mm: MatchupModel, playoff_mps: list[int],
                      rng: np.random.Generator) -> np.ndarray:
    n, k = seeds.shape
    if k < 2:
        return seeds[:, 0] if k else np.full(n, -1)
    size = 1 << (k - 1).bit_length()
    order = _bracket_order(size)  # seed numbers, 1-based; seeds > k are byes
    slots = np.full((n, size), -1)
    for pos, sd in enumerate(order):
        if sd <= k:
            slots[:, pos] = seeds[:, sd - 1]
    rnd = 0
    pc: dict[tuple, float] = {}
    while slots.shape[1] > 1:
        mp = playoff_mps[rnd] if rnd < len(playoff_mps) else (playoff_mps[-1] if playoff_mps else None)
        a, b = slots[:, 0::2], slots[:, 1::2]
        winner = np.where(b == -1, a, np.where(a == -1, b, -1))
        need = winner == -1
        for i, j in zip(*np.nonzero(need)):
            ta, tb = ids[a[i, j]], ids[b[i, j]]
            key = (ta, tb, mp)
            if key not in pc:
                pc[key] = mm.p_win(ta, tb, mp) if mp is not None else 0.5
            winner[i, j] = a[i, j] if rng.random() < pc[key] else b[i, j]
        slots = winner
        rnd += 1
    return slots[:, 0]


def league_strength(ctx: AnalysisContext, results: dict[int, WindowResult] | None = None,
                    with_sim: bool = True) -> list[TeamStrength]:
    results = results or team_windows(ctx)
    mm = MatchupModel(ctx, results)
    reg = ctx.s.settings.regular_season_matchups
    cur = ctx.s.current_matchup_period
    future_reg = [mp for mp in ctx.remaining_mps(include_playoffs=False) if mp > cur] or \
        [mp for mp in ctx.remaining_mps() if mp >= cur]
    ids = [t.id for t in ctx.s.teams]

    def week_len(mp: int) -> float:
        return max(1.0, len(ctx.s.settings.matchup_periods.get(mp, [])) / 7)

    # Points per week over full future regular-season weeks (normalised to 7 days).
    ppw = {}
    for tid in ids:
        vals = [results[tid].by_mp.get(mp, [0, 0])[0] / week_len(mp) for mp in future_reg]
        ppw[tid] = float(np.mean(vals)) if vals else 0.0
    league_avg = float(np.mean(list(ppw.values()))) if ppw else 0.0

    # All-play expected win rate.
    allplay = {tid: 0.0 for tid in ids}
    pairs = list(combinations(ids, 2))
    sample_mps = future_reg
    for mp in sample_mps:
        for a, b in pairs:
            p = mm.p_win(a, b, mp)
            allplay[a] += p
            allplay[b] += 1 - p
    denom = max(1, len(sample_mps) * (len(ids) - 1))
    allplay = {k: v / denom for k, v in allplay.items()}

    # This season's record counts for the share of the regular season already played, the projection for
    # the rest: the projected final win rate, with an average opponent in every remaining week so schedule
    # luck stays out of the future part.
    record: dict[int, tuple[float | None, float]] = {}
    for t in ctx.s.teams:
        played = t.wins + t.losses + t.ties
        weight = min(1.0, played / max(reg, played, 1))
        record[t.id] = ((t.wins + 0.5 * t.ties) / played if played else None, weight)

    def power(tid: int) -> float:
        pct, weight = record[tid]
        return allplay[tid] if pct is None else weight * pct + (1 - weight) * allplay[tid]

    sim = simulate_season(ctx, mm, ppw=ppw) if with_sim else {}
    out = []
    for t in ctx.s.teams:
        ts = TeamStrength(team_id=t.id, name=t.name, abbrev=t.abbrev, owner=t.owner_name,
                          power_score=power(t.id), projected_allplay=allplay[t.id], record_pct=record[t.id][0],
                          record_weight=record[t.id][1] if record[t.id][0] is not None else 0.0,
                          ppw=ppw[t.id], ppw_vs_avg=ppw[t.id] - league_avg,
                          wins=t.wins, losses=t.losses, ties=t.ties)
        if sim:
            for k, v in sim[t.id].items():
                setattr(ts, k, v)
        opp = next(((m.away_team_id if m.home_team_id == t.id else m.home_team_id) for m in ctx.s.matchups
                    if m.matchup_period == cur and t.id in (m.home_team_id, m.away_team_id)), None)
        if opp is not None and cur <= max(ctx.s.settings.matchup_periods or {0: []}):
            my, _ = mm.dist(t.id, cur)
            th, _ = mm.dist(opp, cur)
            ts.current = {"matchup_period": cur, "opponent_id": opp, "proj_score": my, "opp_proj_score": th,
                          "win_prob": mm.p_win(t.id, opp, cur)}
        ts.explanation = explain_team(ctx, t.id, results, ppw, league_avg, mm, future_reg)
        out.append(ts)
    out.sort(key=lambda x: (-x.power_score, -x.projected_allplay))
    for i, ts in enumerate(out, 1):
        ts.rank = i
    _add_summaries(ctx, out)
    return out


def explain_team(ctx: AnalysisContext, team_id: int, results: dict[int, WindowResult], ppw: dict[int, float],
                 league_avg: float, mm: MatchupModel, future_reg: list[int]) -> dict:
    res = results[team_id]
    n_days = max(1, len(ctx.days))
    weeks = n_days / 7
    scoring = ctx.scoring
    ex: dict = {}
    # Stat breakdown (points leagues): weekly fantasy points from each scoring stat vs league average.
    if ctx.is_points:
        def by_stat(r: WindowResult) -> dict[str, float]:
            return {k: v / weeks for k, v in scoring.breakdown(r.stats).items()}

        mine = by_stat(res)
        avg = {}
        for r in results.values():
            for k, v in by_stat(r).items():
                avg[k] = avg.get(k, 0.0) + v / len(results)
        ex["stat_breakdown"] = sorted(
            ({"stat": k, "pts_per_week": mine[k], "league_avg": avg[k], "diff": mine[k] - avg[k]} for k in mine),
            key=lambda x: -abs(x["diff"]))
    else:
        cats = ctx.scoring.categories
        others = [t.id for t in ctx.s.teams if t.id != team_id]
        mp = future_reg[0] if future_reg else ctx.s.current_matchup_period
        cat_p = {c: 0.0 for c in cats}
        for o in others:
            for c, p in mm.cat_probs(team_id, o, mp).items():
                cat_p[c] += p / max(1, len(others))
        ex["category_win_prob"] = [{"category": c, "win_prob": p} for c, p in cat_p.items()]
    total = sum(res.points.values()) or 1.0
    top = sorted(res.points.items(), key=lambda x: -x[1])
    ex["players"] = [
        {"player_id": pid, "name": ctx.s.players[pid].name, "per_week": v / weeks, "share": v / total,
         "starts": res.starts.get(pid, 0.0), "games": res.games.get(pid, 0.0)}
        for pid, v in top
    ]
    all_empty = [r.empty_slot_games / weeks for r in results.values()]
    all_bench = [r.bench_games / weeks for r in results.values()]
    ex["depth"] = {
        "empty_slots_per_week": res.empty_slot_games / weeks,
        "league_avg_empty": float(np.mean(all_empty)),
        "bench_games_per_week": res.bench_games / weeks,
        "league_avg_bench": float(np.mean(all_bench)),
    }
    roster = ctx.team_roster(team_id)
    inj = []
    for pid in roster:
        pr = ctx.proj[pid]
        if pr.games_out > 0:
            inj.append({"player_id": pid, "name": pr.name, "status": ctx.s.players[pid].injury_status,
                        "games_out": pr.games_out, "fp_lost": pr.games_out * pr.fp_pg * pr.availability})
    ex["injuries"] = sorted(inj, key=lambda x: -x["fp_lost"])
    games_week = sum(res.games.values()) / weeks
    league_games = float(np.mean([sum(r.games.values()) / weeks for r in results.values()]))
    ex["schedule"] = {"player_games_per_week": games_week, "league_avg": league_games}
    return ex


def _add_summaries(ctx: AnalysisContext, teams: list[TeamStrength]) -> None:
    for ts in teams:
        ex = ts.explanation
        bits = []
        if ctx.is_points:
            bits.append(f"Projects {ts.ppw:,.0f} pts/week ({ts.ppw_vs_avg:+,.0f} vs league average)")
            sb = [s for s in ex.get("stat_breakdown", []) if abs(s["diff"]) >= 5]
            pos = [s for s in sb if s["diff"] > 0][:2]
            neg = [s for s in sb if s["diff"] < 0][:2]
            if pos:
                bits.append("edges in " + ", ".join(f"{s['stat']} ({s['diff']:+.0f})" for s in pos))
            if neg:
                bits.append("trails in " + ", ".join(f"{s['stat']} ({s['diff']:+.0f})" for s in neg))
        else:
            cw = sorted(ex.get("category_win_prob", []), key=lambda x: -x["win_prob"])
            if cw:
                bits.append("strongest in " + ", ".join(c["category"] for c in cw[:3]))
                bits.append("weakest in " + ", ".join(c["category"] for c in cw[-2:]))
        d = ex.get("depth", {})
        if d and d["empty_slots_per_week"] > d["league_avg_empty"] + 2:
            bits.append(f"leaves {d['empty_slots_per_week']:.0f} starting slots empty per week")
        if ex.get("injuries"):
            worst = ex["injuries"][0]
            bits.append(f"injury drag from {worst['name']} (~{worst['games_out']:.0f} games)")
        sched = ex.get("schedule", {})
        if sched and abs(sched["player_games_per_week"] - sched["league_avg"]) >= 1.5:
            more = sched["player_games_per_week"] > sched["league_avg"]
            bits.append(("favourable" if more else "light") + " schedule volume")
        if ts.record_weight > 0:
            rec = f"{ts.wins}-{ts.losses}" + (f"-{ts.ties}" if ts.ties else "")
            bits.append(f"the {rec} record counts for {ts.record_weight:.0%} of the power score")
        ex["summary"] = "; ".join(bits) + "."
