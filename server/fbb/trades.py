"""Trade evaluation and search.

A trade is judged by what it does to each team's *lineup-adjusted* rest-of-
season output, not by summing player values: the day-by-day optimal lineup
simulation captures positional fit, schedule overlap (two players whose games
collide can't both start), bench depth, and the roster spot a 2-for-1 frees
(filled by the best free agent) or costs (the weakest player is dropped).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from itertools import combinations

from .context import AnalysisContext, WindowResult
from .strength import MatchupModel, simulate_season, team_windows
from .valuation import player_values

ADVANTAGE_PCT = 0.01  # +/-1% of rest-of-season output separates good/neutral/bad
SIM_RUNS = 3000
# Trade finder shortlist, tuned on a 10-team demo league: these give each team a full list of
# suggestions from about 6 partners in every package size, while keeping each team's best deal.
PARTNER_LOSS_WEIGHT = 1.5  # how much the quick score counts the partner's raw-value loss
TOP_SHARE = 0.25  # share of the shortlist taken straight from the top quick scores


@dataclass
class TradeSide:
    team_id: int
    team_name: str
    gives: list[str]
    receives: list[str]
    auto_drops: list[str] = field(default_factory=list)
    auto_adds: list[str] = field(default_factory=list)
    before_ros: float = 0.0
    after_ros: float = 0.0
    delta_ros: float = 0.0
    delta_pct: float = 0.0
    delta_per_week: float = 0.0
    delta_this_week: float = 0.0
    delta_playoffs: float = 0.0
    raw_value_delta: float = 0.0
    before_playoff_odds: float | None = None
    after_playoff_odds: float | None = None
    before_title_odds: float | None = None
    after_title_odds: float | None = None
    verdict: str = "neutral"


def roster_capacity(ctx: AnalysisContext, roster: list[str]) -> int:
    """Active slots plus IR slots that injured players on this roster can occupy."""
    slots = ctx.s.settings.roster_slots
    active = sum(n for code, n in slots.items() if code != "IR")
    injured = sum(1 for p in roster if ctx.s.players[p].injury_status in ("OUT", "INJURY_RESERVE"))
    return active + min(slots.get("IR", 0), injured)


def _ros_value(ctx: AnalysisContext, pid: str) -> float:
    return ctx.value_pg[pid] * ctx.expected_games(pid)


def apply_trade(ctx: AnalysisContext, team_id: int, gives: list[str], receives: list[str],
                taken: set[str]) -> tuple[list[str], list[str], list[str]]:
    """New roster after the trade, dropping/adding to respect roster size."""
    before = ctx.team_roster(team_id)
    roster = [p for p in before if p not in gives] + list(receives)
    drops, adds = [], []
    while len(roster) > roster_capacity(ctx, roster):
        cands = [p for p in roster if p not in receives]
        worst = min(cands, key=lambda p: _ros_value(ctx, p))
        roster.remove(worst)
        drops.append(worst)
    target = min(len(before), roster_capacity(ctx, roster))
    if len(roster) < target:
        fas = sorted((p.id for p in ctx.s.free_agents() if p.id in ctx.proj and p.id not in taken
                      and ctx.proj[p.id].games_out < 3),
                     key=lambda pid: -_ros_value(ctx, pid))
        for pid in fas:
            if len(roster) >= target:
                break
            roster.append(pid)
            adds.append(pid)
            taken.add(pid)
    return roster, drops, adds


def _weeks(ctx: AnalysisContext) -> float:
    return max(1.0, len(ctx.days) / 7)


def _verdict(pct: float) -> str:
    if pct >= ADVANTAGE_PCT:
        return "advantageous"
    if pct <= -ADVANTAGE_PCT:
        return "disadvantageous"
    return "neutral"


class TradeEvaluator:
    def __init__(self, ctx: AnalysisContext, base_results: dict[int, WindowResult] | None = None):
        self.ctx = ctx
        self.base = base_results or team_windows(ctx)
        self.values = player_values(ctx)
        self._base_sim: dict | None = None
        cur = ctx.s.current_matchup_period
        self.this_week = ctx.matchup_days(cur)
        reg = ctx.s.settings.regular_season_matchups
        self.playoff_mps = [mp for mp in ctx.remaining_mps() if mp > reg]

    def base_sim(self) -> dict:
        if self._base_sim is None:
            self._base_sim = simulate_season(self.ctx, MatchupModel(self.ctx, self.base), n=SIM_RUNS)
        return self._base_sim

    def _side(self, team_id: int, gives: list[str], receives: list[str], taken: set[str]) -> tuple[TradeSide, WindowResult]:
        ctx = self.ctx
        roster, drops, adds = apply_trade(ctx, team_id, gives, receives, taken)
        after = ctx.project_window(roster)
        before = self.base[team_id]
        side = TradeSide(team_id=team_id, team_name=ctx.s.team(team_id).name, gives=list(gives),
                         receives=list(receives), auto_drops=drops, auto_adds=adds)
        side.before_ros = before.mean
        side.after_ros = after.mean
        side.delta_ros = after.mean - before.mean
        side.delta_pct = side.delta_ros / before.mean if before.mean else 0.0
        side.delta_per_week = side.delta_ros / _weeks(ctx)
        side.delta_playoffs = sum(after.by_mp.get(mp, [0, 0])[0] - before.by_mp.get(mp, [0, 0])[0]
                                  for mp in self.playoff_mps)
        if self.this_week:
            side.delta_this_week = sum(after.by_mp.get(ctx.s.current_matchup_period, [0, 0])[:1]) - \
                sum(before.by_mp.get(ctx.s.current_matchup_period, [0, 0])[:1])
        side.raw_value_delta = (sum(self.values[p]["ros_value"] for p in receives + adds)
                                - sum(self.values[p]["ros_value"] for p in gives + drops))
        side.verdict = _verdict(side.delta_pct)
        return side, after

    def evaluate(self, team_a: int, team_b: int, a_gives: list[str], b_gives: list[str], with_sim: bool = True) -> dict:
        ctx = self.ctx
        ra = set(ctx.team_roster(team_a))
        rb = set(ctx.team_roster(team_b))
        bad = [p for p in a_gives if p not in ra] + [p for p in b_gives if p not in rb]
        if bad:
            raise ValueError(f"players not on the giving roster: {', '.join(bad)}")
        if not a_gives and not b_gives:
            raise ValueError("a trade needs at least one player")
        taken: set[str] = set()
        side_a, after_a = self._side(team_a, a_gives, b_gives, taken)
        side_b, after_b = self._side(team_b, b_gives, a_gives, taken)

        if with_sim:
            base = self.base_sim()
            results = dict(self.base)
            results[team_a] = after_a
            results[team_b] = after_b
            new = simulate_season(ctx, MatchupModel(ctx, results), n=SIM_RUNS)
            for side in (side_a, side_b):
                side.before_playoff_odds = base[side.team_id]["playoff_odds"]
                side.after_playoff_odds = new[side.team_id]["playoff_odds"]
                side.before_title_odds = base[side.team_id]["title_odds"]
                side.after_title_odds = new[side.team_id]["title_odds"]

        involved = list(dict.fromkeys(a_gives + b_gives + side_a.auto_drops + side_a.auto_adds
                                      + side_b.auto_drops + side_b.auto_adds))
        players = [self._player_row(pid) for pid in involved]
        return {
            "sides": [asdict(side_a), asdict(side_b)],
            "players": players,
            "math": self._math(side_a, side_b),
            "summary": self._summary(side_a, side_b),
            "fairness": _fairness(side_a, side_b),
        }

    def _player_row(self, pid: str) -> dict:
        pr = self.ctx.proj[pid]
        v = self.values[pid]
        p = self.ctx.s.players[pid]
        return {
            "player_id": pid, "name": pr.name, "pro_team": pr.pro_team, "positions": pr.positions,
            "fantasy_team_id": p.fantasy_team_id, "fp_pg": pr.fp_pg, "fp_sd": pr.fp_sd,
            "ros_games": v["ros_games"], "ros_points": pr.fp_pg * v["ros_games"], "vorp": v["vorp"],
            "injury_status": p.injury_status, "games_out": pr.games_out,
            "season_fp_pg": pr.season_fp_pg, "recent_fp_pg": pr.recent_fp_pg,
        }

    def _math(self, a: TradeSide, b: TradeSide) -> list[dict]:
        ctx = self.ctx
        steps = []
        for s in (a, b):
            name = s.team_name
            gave = sum(self.values[p]["ros_value"] for p in s.gives)
            got = sum(self.values[p]["ros_value"] for p in s.receives)
            lines = [
                f"Receives {len(s.receives)} player(s) worth {got:,.0f} projected ROS points "
                f"(FP/G × expected games), gives {len(s.gives)} worth {gave:,.0f}: raw {got - gave:+,.0f}.",
            ]
            if s.auto_drops:
                lines.append("Roster is over the limit, so the lowest-value player is dropped: "
                             + ", ".join(f"{ctx.proj[p].name} ({self.values[p]['ros_value']:,.0f})" for p in s.auto_drops) + ".")
            if s.auto_adds:
                lines.append("A roster spot opens and is filled by the best free agent: "
                             + ", ".join(f"{ctx.proj[p].name} ({self.values[p]['ros_value']:,.0f})" for p in s.auto_adds) + ".")
            lines.append(
                f"Optimal daily lineups over the remaining {len(ctx.days)} days: {s.before_ros:,.0f} → "
                f"{s.after_ros:,.0f} points ({s.delta_ros:+,.0f}, {s.delta_pct:+.1%}). The gap between the raw "
                f"and lineup-adjusted numbers is positional fit, schedule overlap and bench depth."
            )
            if self.playoff_mps:
                lines.append(f"Fantasy-playoff weeks only: {s.delta_playoffs:+,.0f} points.")
            if s.before_playoff_odds is not None:
                lines.append(f"Season simulation ({SIM_RUNS:,} runs): playoff odds {s.before_playoff_odds:.0%} → "
                             f"{s.after_playoff_odds:.0%}, title odds {s.before_title_odds:.0%} → {s.after_title_odds:.0%}.")
            lines.append(f"Verdict: {s.verdict} (threshold ±{ADVANTAGE_PCT:.0%} of rest-of-season output).")
            steps.append({"team_id": s.team_id, "team_name": name, "lines": lines})
        return steps

    def _summary(self, a: TradeSide, b: TradeSide) -> str:
        return (f"{a.team_name}: {a.delta_per_week:+,.1f} pts/week ({a.verdict}). "
                f"{b.team_name}: {b.delta_per_week:+,.1f} pts/week ({b.verdict}).")


def _fairness(a: TradeSide, b: TradeSide) -> dict:
    if a.delta_ros > 0 and b.delta_ros > 0:
        label = "win-win"
    elif a.delta_ros * b.delta_ros < 0:
        label = "lopsided" if abs(a.delta_pct - b.delta_pct) > 0.03 else "balanced"
    else:
        label = "balanced"
    # A rough acceptance likelihood for the other side: logistic in their % change.
    accept = 1 / (1 + math.exp(-b.delta_pct / 0.01))
    return {"label": label, "partner_accept_likelihood": accept}


def find_trades(ctx: AnalysisContext, team_id: int, evaluator: TradeEvaluator | None = None,
                max_give: int = 2, max_get: int = 2, partners: list[int] | None = None,
                candidates: int = 90, limit: int = 12, min_partner_pct: float = -0.004,
                checked: Counter | None = None, exact: bool = False) -> list[dict]:
    """Search 1-for-1 up to max_give-for-max_get trades with every other team.

    With exact=True only packages of exactly max_give and max_get players are
    considered (e.g. strictly 2-for-2). Stage 1 scores every package pair by raw ROS value (with roster-spot
    replacement) and keeps plausible ones; stage 2 runs the full lineup
    simulation for both teams on the best candidates; results are ranked by our
    gain among trades the partner's model also likes (or barely dislikes). If given, `checked`
    counts the packages fully simulated per size ("2-for-1": 22, ...).
    """
    ev = evaluator or TradeEvaluator(ctx)
    vals = {pid: _ros_value(ctx, pid) for pid in ctx.proj}
    fa_vals = sorted((vals[p.id] for p in ctx.s.free_agents() if p.id in vals), reverse=True)
    replacement = fa_vals[2] if len(fa_vals) > 2 else 0.0
    mine = ctx.team_roster(team_id)
    partners = partners or [t.id for t in ctx.s.teams if t.id != team_id]

    def packages(roster: list[str], k: int) -> list[tuple[str, ...]]:
        out = []
        for n in range(k if exact else 1, k + 1):
            out.extend(combinations(roster, n))
        return out

    def spot_adjust(n_out: int, n_in: int, roster: list[str], incoming: tuple) -> float:
        if n_in > n_out:  # must drop worst players
            pool = sorted((vals[p] for p in roster if p not in incoming))
            return -sum(pool[: n_in - n_out])
        if n_out > n_in:
            return replacement * (n_out - n_in)
        return 0.0

    my_pkgs = [p for p in packages(mine, max_give) if all(vals[x] > replacement * 0.5 for x in p)]
    pre: list[tuple[float, int, tuple, tuple]] = []
    for pt in partners:
        theirs = ctx.team_roster(pt)
        their_pkgs = packages(theirs, max_get)
        for give in my_pkgs:
            v_out = sum(vals[x] for x in give)
            for get in their_pkgs:
                v_in = sum(vals[x] for x in get)
                if v_in < 0.7 * v_out or v_in > 1.6 * v_out:
                    continue
                my_raw = v_in - v_out + spot_adjust(len(give), len(get), [p for p in mine if p not in give], get)
                their_raw = v_out - v_in + spot_adjust(len(get), len(give), [p for p in theirs if p not in get], give)
                scale = max(v_out, v_in)
                if their_raw < -0.12 * scale or my_raw < -0.15 * scale:
                    continue
                # Prefer trades good for us that the partner wouldn't lose by: the full simulation drops
                # anything that costs them more than min_partner_pct, so shortlisting lopsided deals wastes it.
                pre.append((my_raw + PARTNER_LOSS_WEIGHT * min(their_raw, 0.0), pt, give, get))
    pre.sort(key=lambda x: -x[0])

    results = []
    for pt, give, get in _shortlist(pre, candidates, max_give, max_get, len(partners)):
        if checked is not None:
            checked[f"{len(give)}-for-{len(get)}"] += 1
        try:
            r = ev.evaluate(team_id, pt, list(give), list(get), with_sim=False)
        except ValueError:
            continue
        a, b = r["sides"]
        if a["delta_ros"] <= 0 or b["delta_pct"] < min_partner_pct:
            continue
        results.append({
            "partner_id": pt,
            "partner_name": ctx.s.team(pt).name,
            "give": list(give),
            "get": list(get),
            "my_delta_ros": a["delta_ros"],
            "my_delta_per_week": a["delta_per_week"],
            "my_delta_pct": a["delta_pct"],
            "partner_delta_ros": b["delta_ros"],
            "partner_delta_pct": b["delta_pct"],
            "fairness": r["fairness"],
            "summary": r["summary"],
        })
    results.sort(key=lambda x: -x["my_delta_ros"])
    # Diversify: at most 3 suggestions per partner and per outgoing player, and room for every package
    # size the user asked for (half the list at most for any one of 1-for-1, 2-for-1, 2-for-2, ...,
    # unless only one size was asked for).
    shape_cap = limit if exact or max_give * max_get == 1 else max(limit // 2, 1)
    counts: Counter = Counter()
    out = []
    for r in results:
        keys = [("partner", r["partner_id"]), *(("give", p) for p in r["give"]),
                ("shape", len(r["give"]), len(r["get"]))]
        caps = [3, *(3 for _ in r["give"]), shape_cap]
        if any(counts[k] >= cap for k, cap in zip(keys, caps)):
            continue
        counts.update(keys)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _shortlist(pre: list[tuple[float, int, tuple, tuple]], candidates: int, max_give: int, max_get: int,
               n_partners: int) -> list[tuple[int, tuple, tuple]]:
    """Pick which quick-scored packages get the full simulation, keeping the mix varied.

    The quick score favours one deal plus every possible throw-in ("A + x for B", "A + y for B", ...).
    Taken in score order those near-copies fill the whole shortlist, so other partners and package
    sizes (2-for-2 especially) never get simulated. So cap each package size, each partner, each
    exact package and each player per partner; if that leaves room, top up in score order.
    """
    shapes = max_give * max_get
    caps = {"shape": max(candidates // shapes, 8), "partner": max(candidates // max(n_partners, 1), 6),
            "package": 2, "player": 4}
    counts: Counter = Counter()
    # The very best quick scores always get simulated, whatever their mix.
    chosen: list[tuple[int, tuple, tuple]] = [(pt, give, get) for _, pt, give, get in pre[: int(candidates * TOP_SHARE)]]
    picked: set[tuple[int, tuple, tuple]] = set(chosen)
    for relaxed in (False, True):
        for _, pt, give, get in pre:
            if len(chosen) >= candidates:
                return chosen
            if (pt, give, get) in picked:
                continue
            keys = [("package", pt, "give", give), ("package", pt, "get", get),
                    *(("player", pt, p) for p in give + get)]
            if not relaxed:
                keys += [("shape", len(give), len(get)), ("partner", pt)]
            if any(counts[k] >= caps[k[0]] for k in keys):
                continue
            counts.update(keys)
            picked.add((pt, give, get))
            chosen.append((pt, give, get))
    return chosen
