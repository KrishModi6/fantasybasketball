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
from itertools import combinations, permutations

from .context import AnalysisContext, WindowResult
from .strength import MatchupModel, simulate_season, team_windows
from .valuation import player_values

ADVANTAGE_PCT = 0.01  # +/-1% of rest-of-season output separates good/neutral/bad
SIM_RUNS = 3000
# Trade finder shortlist, tuned on a 10-team demo league: these give each team a full list of
# suggestions from about 6 partners in every package size, while keeping each team's best deal.
PARTNER_LOSS_WEIGHT = 1.5  # how much the quick score counts the partner's raw-value loss
# Share of the loss the partner accepts (min_partner_pct) that the quick score treats as free. At 0 it
# shortlisted only swaps of equal raw value, which the simulation scores near 0 for both sides (the best
# 2-for-2 gained 0.45 pts/week, 3-for-3 0.35); at 0.8 they gain about 4. At 1 more land just past the line.
PARTNER_SLACK = 0.8
TOP_SHARE = 0.25  # share of the shortlist taken straight from the top quick scores
EVEN_SIZES = frozenset({(1, 1), (2, 2)})  # the default: the same number of players each way
# Three-team rotations fully simulated per search: each runs three lineup windows (about 15 ms), so 120 take
# about 2 s on a 10-team league, which reaches the best rotation with most partner pairs.
THREE_TEAM_CANDIDATES = 120


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
        return self.evaluate_multi({team_a: {p: team_b for p in a_gives}, team_b: {p: team_a for p in b_gives}}, with_sim)

    def evaluate_multi(self, sends: dict[int, dict[str, int]], with_sim: bool = True) -> dict:
        """A trade between two or three teams: sends[team] maps each player it gives to the team that gets him."""
        ctx = self.ctx
        teams = list(sends)
        if not 2 <= len(teams) <= 3:
            raise ValueError("a trade is between two or three teams")
        bad = [p for t, out in sends.items() for p in out if p not in set(ctx.team_roster(t))]
        if bad:
            raise ValueError(f"players not on the giving roster: {', '.join(bad)}")
        if not any(sends.values()):
            raise ValueError("a trade needs at least one player")
        for t, out in sends.items():
            for p, to in out.items():
                if to == t or to not in sends:
                    raise ValueError(f"{ctx.s.players[p].name} has to go to another team in the trade")
        receives = {t: [p for out in sends.values() for p, to in out.items() if to == t] for t in teams}
        idle = [ctx.s.team(t).name for t in teams if not sends[t] and not receives[t]]
        if len(teams) > 2 and idle:
            raise ValueError(f"{', '.join(idle)} must send or get at least one player")
        taken: set[str] = set()
        sides: list[TradeSide] = []
        after: dict[int, WindowResult] = {}
        for t in teams:
            side, after[t] = self._side(t, list(sends[t]), receives[t], taken)
            sides.append(side)

        if with_sim:
            base = self.base_sim()
            new = simulate_season(ctx, MatchupModel(ctx, {**self.base, **after}), n=SIM_RUNS)
            for side in sides:
                side.before_playoff_odds = base[side.team_id]["playoff_odds"]
                side.after_playoff_odds = new[side.team_id]["playoff_odds"]
                side.before_title_odds = base[side.team_id]["title_odds"]
                side.after_title_odds = new[side.team_id]["title_odds"]

        involved = list(dict.fromkeys([p for out in sends.values() for p in out]
                                      + [p for s in sides for p in s.auto_drops + s.auto_adds]))
        players = [self._player_row(pid) for pid in involved]
        return {
            "teams": teams,
            "moves": [{"player_id": p, "from_team": t, "to_team": to} for t, out in sends.items() for p, to in out.items()],
            "sides": [asdict(s) for s in sides],
            "players": players,
            "math": self._math(sides, sends),
            "summary": " ".join(f"{s.team_name}: {s.delta_per_week:+,.1f} pts/week ({s.verdict})." for s in sides),
            "fairness": _fairness(sides),
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

    def _math(self, sides: list[TradeSide], sends: dict[int, dict[str, int]]) -> list[dict]:
        ctx = self.ctx
        steps = []
        for s in sides:
            name = s.team_name
            gave = sum(self.values[p]["ros_value"] for p in s.gives)
            got = sum(self.values[p]["ros_value"] for p in s.receives)
            lines = []
            if len(sides) > 2:  # with three teams, say who each player goes to and comes from
                out = [f"{ctx.proj[p].name} to {ctx.s.team(to).name}" for p, to in sends[s.team_id].items()]
                inc = [f"{ctx.proj[p].name} from {ctx.s.team(t).name}" for t, o in sends.items() for p, to in o.items()
                       if to == s.team_id]
                lines.append(f"Sends {', '.join(out) or 'nobody'}; gets {', '.join(inc) or 'nobody'}.")
            lines += [
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


def _fairness(sides: list[TradeSide]) -> dict:
    pcts = [s.delta_pct for s in sides]
    if all(s.delta_ros > 0 for s in sides):
        label = "win-win" if len(sides) == 2 else "win-win-win"
    elif any(s.delta_ros > 0 for s in sides) and any(s.delta_ros < 0 for s in sides):
        label = "lopsided" if max(pcts) - min(pcts) > 0.03 else "balanced"
    else:
        label = "balanced"
    # A rough acceptance likelihood for the other side(s): logistic in each partner's % change, and in a
    # three-team trade both partners have to say yes.
    accept = math.prod(1 / (1 + math.exp(-s.delta_pct / 0.01)) for s in sides[1:])
    return {"label": label, "partner_accept_likelihood": accept}


def _replacement(ctx: AnalysisContext, vals: dict[str, float]) -> float:
    """ROS value of the free agent a freed roster spot picks up (the third best, as the best go fast)."""
    fa_vals = sorted((vals[p.id] for p in ctx.s.free_agents() if p.id in vals), reverse=True)
    return fa_vals[2] if len(fa_vals) > 2 else 0.0


def _spot_adjust(vals: dict[str, float], replacement: float, roster: list[str], outgoing: tuple, n_in: int) -> float:
    """Raw value of the roster spots a package frees (a free agent fills them) or costs (the worst players go)."""
    n_out = len(outgoing)
    if n_in > n_out:  # must drop worst players
        pool = sorted(vals[p] for p in roster if p not in outgoing)
        return -sum(pool[: n_in - n_out])
    if n_out > n_in:
        return replacement * (n_out - n_in)
    return 0.0


def find_trades(ctx: AnalysisContext, team_id: int, evaluator: TradeEvaluator | None = None,
                max_give: int = 2, max_get: int = 2, partners: list[int] | None = None,
                candidates: int = 90, limit: int = 12, min_partner_pct: float = -0.004,
                checked: Counter | None = None, exact: bool = False,
                sizes: frozenset[tuple[int, int]] | None = None) -> list[dict]:
    """Search trades with every other team.

    Package sizes: the (give, get) pairs in ``sizes`` (EVEN_SIZES for 1-for-1 and 2-for-2); else with
    exact=True only max_give-for-max_get; else every size up to max_give-for-max_get. Stage 1 scores every package pair by raw ROS value (with roster-spot
    replacement) and keeps plausible ones; stage 2 runs the full lineup
    simulation for both teams on the best candidates; results are ranked by our
    gain among trades the partner's model also likes (or barely dislikes). If given, `checked`
    counts the packages fully simulated per size ("2-for-1": 22, ...).
    """
    ev = evaluator or TradeEvaluator(ctx)
    vals = {pid: _ros_value(ctx, pid) for pid in ctx.proj}
    replacement = _replacement(ctx, vals)
    mine = ctx.team_roster(team_id)
    partners = partners or [t.id for t in ctx.s.teams if t.id != team_id]
    if sizes:
        allowed = set(sizes)
    elif exact:
        allowed = {(max_give, max_get)}
    else:
        allowed = {(g, r) for g in range(1, max_give + 1) for r in range(1, max_get + 1)}

    def packages(roster: list[str], counts: set[int]) -> list[tuple[tuple[str, ...], float]]:
        """Every package of `counts` players with its raw value, summed once instead of once per pairing."""
        out = []
        for n in sorted(counts):
            out.extend((p, sum(vals[x] for x in p)) for p in combinations(roster, n))
        return out

    # 3-for-3 pairs 286 packages a side on a 13-man roster, so this loop stays lean: package values are
    # summed once, and the roster spot math only runs for uneven sizes (even trades free no spot).
    my_pkgs = [(p, v) for p, v in packages(mine, {g for g, _ in allowed}) if all(vals[x] > replacement * 0.5 for x in p)]
    pre: list[tuple[float, int, tuple, tuple]] = []
    for pt in partners:
        theirs = ctx.team_roster(pt)
        slack = PARTNER_SLACK * -min_partner_pct * ev.base[pt].mean  # raw value they can lose and still accept
        their_pkgs = packages(theirs, {r for _, r in allowed})
        for give, v_out in my_pkgs:
            for get, v_in in their_pkgs:
                if (len(give), len(get)) not in allowed:
                    continue
                if v_in < 0.7 * v_out or v_in > 1.6 * v_out:
                    continue
                my_raw = v_in - v_out
                their_raw = v_out - v_in
                if len(give) != len(get):
                    my_raw += _spot_adjust(vals, replacement, mine, give, len(get))
                    their_raw += _spot_adjust(vals, replacement, theirs, get, len(give))
                scale = max(v_out, v_in)
                if their_raw < -0.12 * scale or my_raw < -0.15 * scale:
                    continue
                # Prefer trades good for us that the partner still accepts: the full simulation drops anything
                # that costs them more than min_partner_pct, so shortlisting lopsided deals wastes it.
                pre.append((my_raw + PARTNER_LOSS_WEIGHT * min(their_raw + slack, 0.0), pt, give, get))
    pre.sort(key=lambda x: -x[0])

    results = []
    for pt, give, get in _shortlist(pre, candidates, len(allowed), len(partners)):
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
    shape_cap = limit if len(allowed) == 1 else max(limit // 2, 1)
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


def _shortlist(pre: list[tuple[float, int, tuple, tuple]], candidates: int, n_shapes: int,
               n_partners: int) -> list[tuple[int, tuple, tuple]]:
    """Pick which quick-scored packages get the full simulation, keeping the mix varied.

    The quick score favours one deal plus every possible throw-in ("A + x for B", "A + y for B", ...).
    Taken in score order those near-copies fill the whole shortlist, so other partners and package
    sizes (2-for-2 especially) never get simulated. So cap each package size, each partner, each
    exact package and each player per partner; if that leaves room, top up in score order.
    """
    caps = {"shape": max(candidates // n_shapes, 8), "partner": max(candidates // max(n_partners, 1), 6),
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


def find_offers(ctx: AnalysisContext, team_id: int, targets: list[str], evaluator: TradeEvaluator | None = None,
                give_counts: frozenset[int] = frozenset({1, 2, 3}), candidates: int = 48, limit: int = 10,
                min_partner_pct: float = -0.004, checked: Counter | None = None) -> dict:
    """What to offer for one to three players on another team.

    What you get is fixed. Every package of your players in the ``give_counts`` sizes is screened by raw
    ROS value (with roster-spot replacement), the most promising are fully simulated for both teams, and
    the offers the partner's projection accepts (falls no more than min_partner_pct, as in find_trades)
    are ranked by your gain, so the first is the cheapest offer that should get it done. If none
    qualifies, ``closest`` holds the offers the partner refuses by the least. ValueError (with a message
    for the user) if the targets aren't 1 to 3 players on one other team.
    """
    targets = list(dict.fromkeys(targets))
    if not targets:
        raise ValueError("Pick at least one player you want.")
    if len(targets) > 3:
        raise ValueError("Pick up to 3 players you want. Untick one and search again.")
    owner = {e.player_id: t.id for t in ctx.s.teams for e in t.roster}
    for pid in targets:
        name = ctx.s.players[pid].name if pid in ctx.s.players else None
        if name is None:
            raise ValueError("That player isn't in this league anymore. Refresh the page and pick again.")
        if pid not in owner:
            raise ValueError(f"{name} is a free agent, so there's no one to trade with. Add them from the Players page.")
        if pid not in ctx.proj:
            raise ValueError(f"Courtside has no projection for {name} yet, so it can't value a trade for them.")
        if owner[pid] == team_id:
            raise ValueError(f"{name} is already on {ctx.s.team(team_id).name}. Pick a player from another team.")
    partners = {owner[pid] for pid in targets}
    if len(partners) > 1:
        raise ValueError("Pick players from one team. For players on two teams, search each team separately.")
    partner = partners.pop()
    if not give_counts or not give_counts <= {1, 2, 3}:
        raise ValueError("Offer 1, 2 or 3 of your players.")

    ev = evaluator or TradeEvaluator(ctx)
    offers, closest = _package_search(ctx, ev, team_id, [partner], targets, False, give_counts, candidates, limit,
                                      min_partner_pct, checked)
    return {"partner_id": partner, "partner_name": ctx.s.team(partner).name, "get": targets,
            "offers": offers, "closest": closest}


def find_asks(ctx: AnalysisContext, team_id: int, players: list[str], partner: int | None = None,
              evaluator: TradeEvaluator | None = None, get_counts: frozenset[int] = frozenset({1, 2, 3}),
              candidates: int = 48, limit: int = 10, min_partner_pct: float = -0.004,
              checked: Counter | None = None) -> dict:
    """What to ask for one to three of your players from a team that wants them (partner=None: every team).

    The mirror of find_offers: what you give is fixed, and every package of the partner's players in the
    ``get_counts`` sizes is screened, simulated and ranked the same way, so the first is the most you can
    ask for that the partner's projection still accepts. ValueError (with a message for the user) if the
    players aren't 1 to 3 of your own.
    """
    players = list(dict.fromkeys(players))
    if not players:
        raise ValueError("Pick at least one of your players to shop.")
    if len(players) > 3:
        raise ValueError("Pick up to 3 of your players. Untick one and search again.")
    mine = set(ctx.team_roster(team_id))
    for pid in players:
        name = ctx.s.players[pid].name if pid in ctx.s.players else None
        if name is None:
            raise ValueError("That player isn't in this league anymore. Refresh the page and pick again.")
        if pid not in mine:
            raise ValueError(f"{name} isn't on {ctx.s.team(team_id).name}. Pick one of your own players.")
        if pid not in ctx.proj:
            raise ValueError(f"Courtside has no projection for {name} yet, so it can't value a trade for them.")
    others = [t.id for t in ctx.s.teams if t.id != team_id]
    if partner is not None and partner not in others:
        raise ValueError("Pick another team in this league.")
    if not get_counts or not get_counts <= {1, 2, 3}:
        raise ValueError("Ask for 1, 2 or 3 players.")

    ev = evaluator or TradeEvaluator(ctx)
    partners = [partner] if partner is not None else others
    # Every team at once screens nine times the packages, so it simulates twice as many.
    offers, closest = _package_search(ctx, ev, team_id, partners, players, True, get_counts,
                                      candidates if partner is not None else candidates * 2, limit, min_partner_pct, checked)
    return {"partner_id": partner, "partner_name": ctx.s.team(partner).name if partner is not None else None,
            "give": players, "offers": offers, "closest": closest}


def _package_search(ctx: AnalysisContext, ev: TradeEvaluator, team_id: int, partners: list[int], fixed: list[str],
                    fixed_is_mine: bool, counts: frozenset[int], candidates: int, limit: int, min_partner_pct: float,
                    checked: Counter | None) -> tuple[list[dict], list[dict]]:
    """Two-team trades with one side fixed, for find_offers and find_asks.

    With fixed_is_mine, ``fixed`` is what team_id sends and the packages come from each partner's roster;
    otherwise ``fixed`` is what team_id gets and the packages are team_id's own. Every package of ``counts``
    players is screened by raw ROS value (with roster-spot replacement), the most promising are fully
    simulated for both teams, and the trades the partner's projection accepts (falls no more than
    min_partner_pct, as in find_trades) are ranked by team_id's gain. An ask (fixed_is_mine) also has to be
    fair to team_id: one that costs it more than min_partner_pct isn't worth asking for. Returns those,
    diversified, and, when there are none, the refusals that came closest.
    """
    vals = {pid: _ros_value(ctx, pid) for pid in ctx.proj}
    replacement = _replacement(ctx, vals)
    mine = ctx.team_roster(team_id)
    v_fixed = sum(vals[x] for x in fixed)
    pre: list[tuple[float, int, tuple, tuple]] = []
    for pt in partners:
        theirs = ctx.team_roster(pt)
        slack = PARTNER_SLACK * -min_partner_pct * ev.base[pt].mean  # as in find_trades
        for n in sorted(counts):
            for pkg in combinations(theirs if fixed_is_mine else mine, n):
                if not all(vals[x] > replacement * 0.5 for x in pkg):
                    continue
                v_pkg = sum(vals[x] for x in pkg)
                give, get, v_out, v_in = ((tuple(fixed), pkg, v_fixed, v_pkg) if fixed_is_mine
                                          else (pkg, tuple(fixed), v_pkg, v_fixed))
                my_raw = v_in - v_out + _spot_adjust(vals, replacement, mine, give, len(get))
                their_raw = v_out - v_in + _spot_adjust(vals, replacement, theirs, get, len(give))
                # Highest for the trade that keeps the most for team_id while the partner still accepts it.
                pre.append((my_raw + PARTNER_LOSS_WEIGHT * min(their_raw + slack, 0.0), pt, give, get))
    pre.sort(key=lambda x: -x[0])

    def varied(pt: int, give: tuple, get: tuple) -> list[tuple]:
        pkg = get if fixed_is_mine else give
        return [("size", len(pkg)), *(("player", p) for p in pkg), ("partner", pt)]

    # The best quick scores, then a mix, so the shortlist isn't one player plus every throw-in (or one team).
    caps = {"size": max(candidates // len(counts), 8), "player": max(candidates // 4, 4),
            "partner": max(candidates // len(partners), 6)}
    chosen = _pick([x[1:] for x in pre], lambda x: varied(*x), caps, candidates)

    accepted, refused = [], []
    for pt, give, get in chosen:
        if checked is not None:
            checked[f"{len(give)}-for-{len(get)}"] += 1
        try:
            r = ev.evaluate(team_id, pt, list(give), list(get), with_sim=False)
        except ValueError:
            continue
        a, b = r["sides"]
        row = {"partner_id": pt, "partner_name": ctx.s.team(pt).name, "give": list(give), "get": list(get),
               "my_delta_ros": a["delta_ros"], "my_delta_per_week": a["delta_per_week"], "my_delta_pct": a["delta_pct"],
               "partner_delta_ros": b["delta_ros"], "partner_delta_pct": b["delta_pct"],
               "fairness": r["fairness"], "summary": r["summary"]}
        if b["delta_pct"] < min_partner_pct:
            refused.append(row)
        elif not fixed_is_mine or a["delta_pct"] >= min_partner_pct:
            accepted.append(row)
    accepted.sort(key=lambda x: -x["my_delta_ros"])
    # Diversify as find_trades does: at most 3 built on any one varied player (and, across teams, any one
    # team), and room for every package size asked for.
    offers = _spread(accepted, lambda r: varied(r["partner_id"], tuple(r["give"]), tuple(r["get"])),
                     {"size": limit if len(counts) == 1 else max(limit // 2, 1), "player": 3,
                      "partner": 3 if len(partners) > 1 else limit}, limit)
    refused.sort(key=lambda x: -x["partner_delta_pct"])
    return offers, [] if offers else refused[:3]


def find_three_team_trades(ctx: AnalysisContext, team_id: int, evaluator: TradeEvaluator | None = None,
                           partner: int | None = None, candidates: int = THREE_TEAM_CANDIDATES, limit: int = 12,
                           min_partner_pct: float = -0.004, checked: Counter | None = None) -> list[dict]:
    """Three-team rotations: team_id sends a player to P, P sends one to Q, and Q sends one back to team_id.

    Everyone gives one player and gets one from a different team, which finds deals no two-team trade can
    (you want Q's player, Q wants P's, P wants yours). Every ordered pair of partners (so both directions)
    is screened by raw ROS value as in find_trades, the most promising are fully simulated for all three
    teams, and the rotations neither partner's projection refuses (falls more than min_partner_pct) are
    ranked by team_id's gain. With ``partner``, only rotations that include that team.
    """
    ev = evaluator or TradeEvaluator(ctx)
    vals = {pid: _ros_value(ctx, pid) for pid in ctx.proj}
    replacement = _replacement(ctx, vals)
    others = [t.id for t in ctx.s.teams if t.id != team_id]
    pools = {t: [p for p in ctx.team_roster(t) if vals[p] > replacement * 0.5] for t in [team_id, *others]}
    slack = {t: PARTNER_SLACK * -min_partner_pct * ev.base[t].mean for t in others}  # as in find_trades

    def plausible(v_in: float, v_out: float, loss: float) -> bool:
        """Gets 70-160% of what it gives and loses no more than `loss` of the bigger side, as find_trades screens."""
        return 0.7 * v_out <= v_in <= 1.6 * v_out and v_in - v_out >= -loss * max(v_in, v_out)

    pre: list[tuple[float, int, int, str, str, str]] = []
    for p, q in permutations(others, 2):
        if partner is not None and partner not in (p, q):
            continue
        for a in pools[team_id]:  # you send a to P
            va = vals[a]
            for b in pools[p]:  # P gets a and sends b to Q
                vb = vals[b]
                if not plausible(va, vb, 0.12):
                    continue
                for c in pools[q]:  # Q gets b and sends c to you
                    vc = vals[c]
                    if not (plausible(vb, vc, 0.12) and plausible(vc, va, 0.15)):
                        continue
                    partners_raw = min(va - vb + slack[p], 0.0) + min(vb - vc + slack[q], 0.0)
                    pre.append((vc - va + PARTNER_LOSS_WEIGHT * partners_raw, p, q, a, b, c))
    pre.sort(key=lambda x: -x[0])
    n_pairs = len({frozenset(x[1:3]) for x in pre})
    caps = {"pair": max(candidates // max(n_pairs, 1), 4), "player": 4}
    chosen = _pick([x[1:] for x in pre], lambda x: [("pair", frozenset(x[:2])), *(("player", pid) for pid in x[2:])],
                   caps, candidates)

    results = []
    for p, q, a, b, c in chosen:
        if checked is not None:
            checked["3-team"] += 1
        try:
            r = ev.evaluate_multi({team_id: {a: p}, p: {b: q}, q: {c: team_id}}, with_sim=False)
        except ValueError:
            continue
        me, *them = r["sides"]
        if me["delta_ros"] <= 0 or min(s["delta_pct"] for s in them) < min_partner_pct:
            continue
        results.append({
            "teams": r["teams"], "moves": r["moves"], "give": [a], "get": [c],
            "my_delta_ros": me["delta_ros"], "my_delta_per_week": me["delta_per_week"], "my_delta_pct": me["delta_pct"],
            "partners": [{"team_id": s["team_id"], "team_name": s["team_name"], "delta_ros": s["delta_ros"],
                          "delta_pct": s["delta_pct"]} for s in them],
            "fairness": r["fairness"], "summary": r["summary"],
        })
    results.sort(key=lambda x: -x["my_delta_ros"])
    # At most 2 rotations with any one pair of partners, and 3 built on any one player you send or get.
    return _spread(results, lambda r: [("pair", frozenset(r["teams"][1:])), ("give", r["give"][0]), ("get", r["get"][0])],
                   {"pair": 2, "give": 3, "get": 3}, limit)


def _pick(ranked: list, keys, caps: dict[str, int], n: int) -> list:
    """The first n of ranked, kept varied: the top TOP_SHARE as ranked, then items while every key (kind, ...)
    from keys(item) is under caps[kind], then, if that leaves room, the rest in rank order."""
    chosen = list(ranked[: int(n * TOP_SHARE)])
    picked = set(chosen)
    counts: Counter = Counter(k for x in chosen for k in keys(x))
    for relaxed in (False, True):
        for x in ranked:
            if len(chosen) >= n:
                return chosen
            ks = keys(x)
            if x in picked or (not relaxed and any(counts[k] >= caps[k[0]] for k in ks)):
                continue
            counts.update(ks)
            picked.add(x)
            chosen.append(x)
    return chosen


def _spread(rows: list[dict], keys, caps: dict[str, int], limit: int) -> list[dict]:
    """Up to `limit` rows in order, skipping any whose keys (kind, ...) already reached caps[kind]."""
    used: Counter = Counter()
    out = []
    for r in rows:
        ks = keys(r)
        if any(used[k] >= caps[k[0]] for k in ks):
            continue
        used.update(ks)
        out.append(r)
        if len(out) >= limit:
            break
    return out
