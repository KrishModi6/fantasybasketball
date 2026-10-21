"""Turn engine objects into compact JSON for the web client."""

from __future__ import annotations

from dataclasses import asdict

from fbb.baseline import RATE_STATS
from fbb.constants import season_label
from fbb.valuation import player_values

from .analysis import LeagueAnalysis

BOX = ["PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FGM", "FGA", "FTM", "FTA", "MIN"]


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _pct(m: float, a: float) -> float | None:
    return round(m / a, 3) if a else None


def player_rows(a: LeagueAnalysis, ids: list[str] | None = None) -> list[dict]:
    ctx = a.ctx
    values = _values(a)
    ids = ids if ids is not None else list(a.proj)
    next7 = ctx.days[:7]
    ranked = sorted(a.proj, key=lambda pid: -values[pid]["ros_value"])
    rank = {pid: i + 1 for i, pid in enumerate(ranked)}
    out = []
    for pid in ids:
        pr = a.proj.get(pid)
        if pr is None:
            continue
        p = a.snapshot.players[pid]
        v = values[pid]
        pg = pr.per_game
        row = {
            "id": pid, "name": pr.name, "pro_team": pr.pro_team, "positions": pr.positions,
            "eligible_slots": [s for s in p.eligible_slots if s not in ("BE", "IR")],
            "injury_status": p.injury_status, "injury_note": p.injury_note,
            "fantasy_team_id": p.fantasy_team_id, "on_waivers": p.on_waivers,
            "age": _r(p.age, 1), "ownership_pct": _r(p.ownership_pct, 1),
            "fp_pg": _r(pr.fp_pg), "fp_sd": _r(pr.fp_sd), "preseason_fp_pg": _r(pr.preseason_fp_pg),
            "season_fp_pg": _r(pr.season_fp_pg), "recent_fp_pg": _r(pr.recent_fp_pg),
            "games_played": pr.games_played, "mpg": _r(pr.mpg, 1), "availability": _r(pr.availability, 3),
            "games_out": _r(pr.games_out, 1), "ros_games": _r(v["ros_games"], 1),
            "ros_value": _r(v["ros_value"], 0), "vorp": _r(v["vorp"], 0), "value_pg": _r(v["value_pg"]),
            "rank": rank[pid], "games_next7": _r(ctx.expected_games(pid, next7), 1),
            "per_game": {k: _r(pg.get(k, 0.0)) for k in BOX},
            "fg_pct": _pct(pg.get("FGM", 0), pg.get("FGA", 0)), "ft_pct": _pct(pg.get("FTM", 0), pg.get("FTA", 0)),
            "flags": pr.flags,
        }
        if ctx.cat_valuer:
            row["gscores"] = {k: _r(x) for k, x in ctx.cat_valuer.gscores(pid).items()}
        out.append(row)
    return out


def _values(a: LeagueAnalysis) -> dict:
    if not hasattr(a, "_values"):
        a._values = player_values(a.ctx)  # type: ignore[attr-defined]
    return a._values  # type: ignore[attr-defined]


def player_detail(a: LeagueAnalysis, pid: str, history_rows: list | None = None) -> dict:
    pr = a.proj[pid]
    p = a.snapshot.players[pid]
    row = player_rows(a, [pid])[0]
    games = []
    for g in reversed(p.recent_games[-20:]):
        d = a.snapshot.schedule.period_dates.get(g.period) if g.season == a.snapshot.settings.season else None
        games.append({"season": g.season, "period": g.period, "date": d.isoformat() if d else None,
                      "team": g.team, "min": _r(g.minutes, 0),
                      "fp": _r(a.engine.fp_scoring.fantasy_points(g.stats), 1),
                      **{k: _r(g.stats.get(k, 0), 0) for k in ("PTS", "REB", "AST", "STL", "BLK", "TO", "3PM")}})
    seasons = []
    for h in p.history[-4:] + ([p.current] if p.current and p.current.gp else []):
        pg = {k: v / h.gp for k, v in h.stats.items()} if h.gp else {}
        seasons.append({"season": h.season, "label": season_label(h.season), "team": h.team, "gp": int(h.gp),
                        "mpg": _r(h.minutes / h.gp, 1) if h.gp else None,
                        "fp_pg": _r(a.engine.fp_scoring.fantasy_points(pg), 1) if pg else None,
                        **{k: _r(pg.get(k, 0), 1) for k in ("PTS", "REB", "AST", "STL", "BLK", "TO", "3PM")}})
    news = [n.model_dump(mode="json") for n in a.snapshot.news if pid in n.player_ids][:8]
    return {
        **row,
        "sources": [asdict(s) | {"per_game": {k: _r(v) for k, v in s.per_game.items() if k in BOX}}
                    for s in pr.sources],
        "adjustments": [asdict(x) for x in pr.adjustments],
        "explanation": a.engine.explain(pr),
        "in_season_weight": _r(pr.in_season_weight, 3),
        "fp_breakdown": {k: _r(v) for k, v in a.engine.fp_scoring.breakdown(pr.per_game).items()},
        "game_log": games,
        "seasons": seasons,
        "news": news,
        "trend": [{"as_of": r.as_of.isoformat(), "fp_pg": r.fp_pg, "mpg": r.mpg, "games_out": r.games_out}
                  for r in (history_rows or [])],
        "modelled_stats": RATE_STATS,
    }


def strength_rows(a: LeagueAnalysis) -> list[dict]:
    return [ts.to_dict() for ts in a.strength()]


def team_detail(a: LeagueAnalysis, team_id: int) -> dict:
    ctx = a.ctx
    t = a.snapshot.team(team_id)
    res = a.windows[team_id]
    weeks = max(1.0, len(ctx.days) / 7)
    slots = {e.player_id: e.slot for e in t.roster}
    rows = player_rows(a, [e.player_id for e in t.roster])
    for r in rows:
        r["slot"] = slots.get(r["id"], "BE")
        r["exp_starts"] = _r(res.starts.get(r["id"], 0.0), 1)
        r["exp_games"] = _r(res.games.get(r["id"], 0.0), 1)
        r["pts_per_week"] = _r(res.points.get(r["id"], 0.0) / weeks, 1)
    st = next((s for s in a.strength() if s.team_id == team_id), None)
    upcoming = []
    for mp in ctx.remaining_mps()[:6]:
        m, v = res.by_mp.get(mp, [0.0, 0.0])
        days = ctx.s.settings.matchup_periods.get(mp, [])
        d0 = ctx.s.schedule.period_dates.get(days[0]) if days else None
        d1 = ctx.s.schedule.period_dates.get(days[-1]) if days else None
        opp = next(((x.away_team_id if x.home_team_id == team_id else x.home_team_id) for x in ctx.s.matchups
                    if x.matchup_period == mp and team_id in (x.home_team_id, x.away_team_id)), None)
        upcoming.append({"matchup_period": mp, "start": d0.isoformat() if d0 else None,
                         "end": d1.isoformat() if d1 else None, "proj_mean": _r(m, 1), "proj_sd": _r(v ** 0.5, 1),
                         "opponent_id": opp, "playoffs": mp > ctx.s.settings.regular_season_matchups})
    return {
        "id": t.id, "name": t.name, "abbrev": t.abbrev, "owner_name": t.owner_name,
        "record": {"wins": t.wins, "losses": t.losses, "ties": t.ties, "points_for": t.points_for,
                   "points_against": t.points_against},
        "roster": sorted(rows, key=lambda r: -(r["pts_per_week"] or 0)),
        "strength": st.to_dict() if st else None,
        "upcoming": upcoming,
        "ros_total": _r(res.mean, 0),
        "weeks_remaining": _r(weeks, 2),
    }
