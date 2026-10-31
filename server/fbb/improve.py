"""Team diagnostics and concrete improvement moves (add/drop, streaming, lineup)."""

from __future__ import annotations

import math

from .context import AnalysisContext, WindowResult
from .strength import normal_cdf
from .valuation import player_values, replacement_fp

TOP_FREE_AGENTS = 30
DROP_CANDIDATES = 4


def _weeks(ctx: AnalysisContext, days: list[int]) -> float:
    return max(1.0, len(days) / 7)


def diagnose(ctx: AnalysisContext, team_id: int, base: WindowResult, league: dict[int, WindowResult]) -> list[dict]:
    """Weaknesses ranked by estimated fantasy points at stake per week."""
    weeks = _weeks(ctx, ctx.days)
    issues = []
    roster = ctx.team_roster(team_id)
    values = player_values(ctx)
    repl = replacement_fp(ctx)

    # 1) Empty starting slots: games you can't fill.
    avg_empty = sum(r.empty_slot_games for r in league.values()) / len(league) / weeks
    empty = base.empty_slot_games / weeks
    if empty > avg_empty + 1:
        lost = (empty - avg_empty) * repl * 0.8
        issues.append({
            "kind": "empty_slots", "severity": lost,
            "title": "Unfilled starting slots",
            "detail": f"About {empty:.1f} starting slot-games per week go unused (league average {avg_empty:.1f}). "
                      f"Players with more games, or with positions you're short on, would fill them.",
        })
    # 2) Bench waste: playable games stuck on the bench (schedule overlap / too many similar players).
    avg_bench = sum(r.bench_games for r in league.values()) / len(league) / weeks
    bench = base.bench_games / weeks
    if bench > avg_bench + 1:
        issues.append({
            "kind": "bench_overflow", "severity": (bench - avg_bench) * repl * 0.5,
            "title": "Too many games on the bench",
            "detail": f"{bench:.1f} player-games per week can't be started (league average {avg_bench:.1f}). "
                      f"Consolidating two players into one better player (2-for-1 trade) converts depth into points.",
        })
    # 3) Sub-replacement players.
    weak = [pid for pid in roster if ctx.value_pg[pid] < repl and ctx.proj[pid].games_out < 5]
    for pid in sorted(weak, key=lambda p: ctx.value_pg[p])[:3]:
        pr = ctx.proj[pid]
        gap = repl - ctx.value_pg[pid]
        issues.append({
            "kind": "below_replacement", "severity": gap * 3.5, "player_id": pid,
            "title": f"{pr.name} projects below replacement",
            "detail": f"{pr.fp_pg:.1f} FP/G vs ~{repl:.1f} available on waivers.",
        })
    # 4) Injuries: games lost, and IR housekeeping.
    team = ctx.s.team(team_id)
    slots = {e.player_id: e.slot for e in team.roster}
    ir_open = ctx.s.settings.roster_slots.get("IR", 0) - sum(1 for e in team.roster if e.slot == "IR")
    for pid in roster:
        pr = ctx.proj[pid]
        if pr.games_out >= 3:
            lost = pr.games_out * pr.fp_pg * pr.availability
            detail = f"Projected to miss ~{pr.games_out:.0f} games (~{lost:,.0f} points)."
            if ir_open > 0 and slots.get(pid) != "IR" and ctx.s.players[pid].injury_status in ("OUT", "INJURY_RESERVE"):
                detail += " Moving him to IR frees a roster spot for a pickup."
            issues.append({"kind": "injury", "severity": lost / weeks, "player_id": pid,
                           "title": f"{pr.name} is out", "detail": detail})
    # 5) Category leagues: weakest categories.
    if not ctx.is_points and ctx.cat_valuer:
        totals: dict[str, float] = {}
        for pid in roster:
            for c, g in ctx.cat_valuer.gscores(pid).items():
                totals[c] = totals.get(c, 0.0) + g * base.starts.get(pid, 0.0) / weeks
        for c, v in sorted(totals.items(), key=lambda x: x[1])[:2]:
            if v < 0:
                issues.append({"kind": "category", "severity": -v, "title": f"Weak in {c}",
                               "detail": f"Your starters contribute {v:+.1f} G-score-weighted starts per week in {c}."})
    issues.sort(key=lambda x: -x["severity"])
    return issues


def _droppable(ctx: AnalysisContext, team_id: int, roster: list[str]) -> list[str]:
    """Players a pickup can replace. Not the one in the IR slot: dropping him frees only that slot, and a
    healthy free agent can't go there (it suggested dropping an injured starter for a pickup ESPN won't allow)."""
    on_ir = {e.player_id for e in ctx.s.team(team_id).roster if e.slot == "IR"}
    return [p for p in roster if p not in on_ir and ctx.s.players[p].injury_status != "INJURY_RESERVE"]


def _swap(ctx: AnalysisContext, roster: list[str], add: str, drop: str) -> tuple[list[str], dict[str, int] | None]:
    """Roster and leaving days (for project_window) after adding ``add`` for ``drop``. A waiver claim drops
    him only when it clears, so he plays until then."""
    new = [p for p in roster if p != drop] + [add]
    if not ctx.s.players[add].on_waivers:
        return new, None
    return new + [drop], {drop: ctx.first_day(add)}


def add_drop_moves(ctx: AnalysisContext, team_id: int, base: WindowResult, limit: int = 8) -> list[dict]:
    roster = ctx.team_roster(team_id)
    values = player_values(ctx)
    fas = [p.id for p in ctx.s.free_agents() if p.id in ctx.proj and ctx.proj[p.id].games_out < 6]
    fas.sort(key=lambda pid: -values[pid]["ros_value"])
    fas = fas[:TOP_FREE_AGENTS]
    droppable = sorted(_droppable(ctx, team_id, roster), key=lambda pid: values[pid]["ros_value"])[:DROP_CANDIDATES]
    weeks = _weeks(ctx, ctx.days)
    cur = ctx.s.current_matchup_period
    week_days = ctx.matchup_days(cur)
    base_week = ctx.project_window(roster, week_days, team_id=team_id).mean if week_days else 0.0
    out = []
    for fa in fas:
        best = None
        for drop in droppable:
            new_roster, leaves = _swap(ctx, roster, fa, drop)
            after = ctx.project_window(new_roster, team_id=team_id, leaves=leaves)
            delta = after.mean - base.mean
            if best is None or delta > best[0]:
                best = (delta, drop, new_roster, leaves)
        if not best or best[0] <= 0:
            continue
        delta, drop, new_roster, leaves = best
        week_delta = (ctx.project_window(new_roster, week_days, team_id=team_id, leaves=leaves).mean - base_week
                      if week_days else 0.0)
        fa_pr, dr_pr = ctx.proj[fa], ctx.proj[drop]
        reasons = []
        if fa_pr.fp_pg > dr_pr.fp_pg:
            reasons.append(f"+{fa_pr.fp_pg - dr_pr.fp_pg:.1f} FP/G")
        g_fa, g_dr = values[fa]["ros_games"], values[drop]["ros_games"]
        if g_fa - g_dr >= 2:
            reasons.append(f"{g_fa - g_dr:.0f} more expected games")
        if dr_pr.games_out > 0:
            reasons.append(f"{dr_pr.name} injured (~{dr_pr.games_out:.0f} games)")
        out.append({
            "add": fa, "add_name": fa_pr.name, "drop": drop, "drop_name": dr_pr.name,
            "delta_ros": delta, "delta_per_week": delta / weeks, "delta_this_week": week_delta,
            "reasons": reasons, "on_waivers": ctx.s.players[fa].on_waivers,
        })
    out.sort(key=lambda x: -x["delta_ros"])
    return out[:limit]


def streaming_moves(ctx: AnalysisContext, team_id: int, limit: int = 6) -> dict:
    """Best one-week pickups for the current matchup (uses an acquisition, drops the lowest-value player)."""
    cur = ctx.s.current_matchup_period
    days = ctx.matchup_days(cur)
    if not days:
        return {"days_left": 0, "moves": []}
    roster = ctx.team_roster(team_id)
    values = player_values(ctx)
    base = ctx.project_window(roster, days, team_id=team_id).mean
    drop = min((p for p in _droppable(ctx, team_id, roster) if ctx.proj[p].games_out < 50),
               key=lambda p: values[p]["vorp"], default=None)
    if drop is None:
        return {"days_left": len(days), "moves": []}

    def games(pid: str) -> float:
        # A pickup plays only once his waiver claim clears, and not in a game that has already started.
        return ctx.expected_games(pid, [d for d in days if d >= ctx.first_day(pid)])

    fas = [p.id for p in ctx.s.free_agents() if p.id in ctx.proj and ctx.proj[p.id].games_out == 0]
    fas.sort(key=lambda pid: -ctx.value_pg[pid] * games(pid))
    moves = []
    for fa in fas[:25]:
        new, leaves = _swap(ctx, roster, fa, drop)
        delta = ctx.project_window(new, days, team_id=team_id, leaves=leaves).mean - base
        if delta > 0:
            moves.append({"add": fa, "add_name": ctx.proj[fa].name, "drop": drop, "drop_name": ctx.proj[drop].name,
                          "games_this_week": games(fa), "delta_this_week": delta,
                          "ros_cost": values[drop]["ros_value"] - values[fa]["ros_value"],
                          "on_waivers": ctx.s.players[fa].on_waivers})
    moves.sort(key=lambda x: -x["delta_this_week"])
    return {"days_left": len(days), "acquisition_limit": ctx.s.settings.acquisition_limit_per_matchup,
            "moves": moves[:limit]}


def lineup_plan(ctx: AnalysisContext, team_id: int, n_days: int = 7) -> list[dict]:
    """Optimal lineups for the next days with a confidence for every start."""
    roster = ctx.team_roster(team_id)
    days = [d for d in ctx.days[:n_days]]
    res = ctx.project_window(roster, days, collect_days=True, team_id=team_id)
    out = []
    for day in res.days:
        d = day["period"]
        gone = {pid for pid, (cut, _) in res.cuts.items() if cut <= d}  # dropped when an injured teammate returned
        roster = [pid for pid in roster if pid not in gone]
        playing = {pid for pid in roster if ctx.p_play(pid).get(d, 0) > 0}
        starters = []
        bench = [pid for pid in day["bench"]]
        for s in day["starters"]:
            pid, slot = s["player_id"], s["slot"]
            j_mask = None
            for j, code in enumerate(ctx.slots.slots):
                if code == slot:
                    j_mask = 1 << j
                    break
            alts = [b for b in bench if j_mask and ctx.mask[b] & j_mask]
            conf, alt_name = 1.0, None
            if alts:
                alt = max(alts, key=lambda b: ctx.value_pg[b] * ctx.p_play(b).get(d, 0))
                ps, pa = ctx.p_play(pid)[d], ctx.p_play(alt)[d]
                ms, ma = ctx.proj[pid].fp_pg * ps, ctx.proj[alt].fp_pg * pa
                sd = math.sqrt(ctx.proj[pid].fp_sd ** 2 + ctx.proj[alt].fp_sd ** 2)
                conf = normal_cdf((ms - ma) / max(sd, 1.0))
                alt_name = ctx.proj[alt].name
            starters.append({"player_id": pid, "name": ctx.proj[pid].name, "slot": slot,
                             "projected": ctx.proj[pid].fp_pg * ctx.p_play(pid)[d], "confidence": conf,
                             "alternative": alt_name})
        idle = [pid for pid in roster if pid not in playing]
        date = ctx.s.schedule.period_dates.get(d)
        out.append({
            "period": d, "date": date.isoformat() if date else None, "expected": day["expected"],
            "starters": starters,
            "bench": [{"player_id": b, "name": ctx.proj[b].name, "projected": ctx.proj[b].fp_pg * ctx.p_play(b)[d]}
                      for b in bench],
            "no_game": [{"player_id": pid, "name": ctx.proj[pid].name} for pid in idle],
        })
    return out


def team_report(ctx: AnalysisContext, team_id: int, league: dict[int, WindowResult]) -> dict:
    base = league[team_id]
    return {
        "weaknesses": diagnose(ctx, team_id, base, league),
        "moves": add_drop_moves(ctx, team_id, base),
        "streaming": streaming_moves(ctx, team_id),
    }
