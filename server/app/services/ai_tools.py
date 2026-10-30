"""Functions the AI assistant can call.

Each tool wraps an engine computation and returns compact, rounded JSON. The model is told to
use only these numbers, so every recommendation traces back to Courtside's projections,
lineup simulations, trade evaluator or matchup simulator.
"""

from __future__ import annotations

from typing import Any, Callable

from fbb.connectors.fileimport import normalize_name
from fbb.improve import add_drop_moves, diagnose, streaming_moves
from fbb.matchup_sim import MatchupSimulator
from fbb.trades import EVEN_SIZES, find_asks, find_offers, find_three_team_trades, find_trades

from .analysis import LeagueAnalysis
from .present import player_detail, player_rows, team_detail


class ToolError(Exception):
    """A user-facing problem with tool arguments (unknown player, ambiguous name, ...)."""


def _r(x: Any, n: int = 1) -> Any:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    return round(float(x)) if n == 0 else round(float(x), n)


def _slim_player(row: dict) -> dict:
    return {
        "name": row["name"], "id": row["id"], "nba_team": row["pro_team"], "positions": row["positions"],
        "fp_per_game": _r(row["fp_pg"]), "game_to_game_sd": _r(row["fp_sd"]), "minutes": _r(row["mpg"]),
        "games_next_7_days": _r(row["games_next7"]), "rest_of_season_value": _r(row["ros_value"], 0),
        "value_over_replacement": _r(row["vorp"], 0), "season_fp_per_game": _r(row["season_fp_pg"]),
        "last10_fp_per_game": _r(row["recent_fp_pg"]),
        # NBA.com tracking: what his shots, passes, rebound chances and deflections were worth this season, and
        # how far his actual FP/G ran above (lucky, sell-high) or below (unlucky, buy-low) that.
        "expected_fp_per_game_so_far": _r(row.get("xfp_pg")), "luck_fp_per_game_so_far": _r(row.get("luck_pg")),
        "injury": row["injury_status"],
        "games_out": _r(row["games_out"]), "rostered_on_espn_pct": _r(row["ownership_pct"], 0),
    }


class ToolBox:
    def __init__(self, analysis: LeagueAnalysis, my_team_id: int | None):
        self.a = analysis
        self.ctx = analysis.ctx
        self.snap = analysis.snapshot
        self.my_team_id = my_team_id
        self._funcs: dict[str, Callable[..., Any]] = {
            "league_overview": self.league_overview,
            "team_roster": self.team_roster,
            "free_agents": self.free_agents,
            "waiver_recommendations": self.waiver_recommendations,
            "player_details": self.player_details,
            "evaluate_trade": self.evaluate_trade,
            "evaluate_three_team_trade": self.evaluate_three_team_trade,
            "trade_ideas": self.trade_ideas,
            "trade_offers": self.trade_offers,
            "what_to_ask_for": self.what_to_ask_for,
            "three_team_trade_ideas": self.three_team_trade_ideas,
            "simulate_matchup": self.simulate_matchup,
            "league_news": self.league_news,
        }

    # -- schemas -----------------------------------------------------------------------

    @staticmethod
    def schemas() -> list[dict]:
        def fn(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> dict:
            return {"type": "function", "name": name, "description": description,
                    "parameters": {"type": "object", "properties": props or {}, "required": required or [],
                                   "additionalProperties": False}}

        team = {"type": "string", "description": "Team name or id. Omit for the user's own team."}
        return [
            fn("league_overview", "Power rankings for every team: record, projected points per week, power score (this "
               "season's record for the share of the season played, projected all-play win rate for the rest), "
               "projected final record, playoff and title odds from 5,000 season simulations."),
            fn("team_roster", "A team's roster with each player's projection, schedule, injury and weekly contribution, "
               "plus the team's diagnosed weak spots.", {"team": team}),
            fn("free_agents", "Best available free agents by rest-of-season value (default), games in the next 7 days, or "
               "fantasy points per game.", {
                   "position": {"type": "string", "enum": ["PG", "SG", "SF", "PF", "C"], "description": "Optional position filter."},
                   "sort_by": {"type": "string", "enum": ["rest_of_season", "games_next_7_days", "fp_per_game"]},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 25},
               }),
            fn("waiver_recommendations", "Engine-simulated add/drop moves for a team (rest-of-season gain per week, this week's "
               "gain) and the best one-week streaming pickups.", {"team": team}),
            fn("player_details", "Full projection breakdown for one player: sources and weights, adjustments, projected box "
               "score, recent games, season history and recent news headlines.",
               {"player": {"type": "string", "description": "Player name."}}, ["player"]),
            fn("evaluate_trade", "Evaluate a trade with lineup-adjusted rest-of-season simulation for both teams, playoff and "
               "title odds before/after, and a verdict.", {
                   "other_team": {"type": "string", "description": "The trade partner (name or id)."},
                   "give": {"type": "array", "items": {"type": "string"}, "description": "Players the user's team sends."},
                   "get": {"type": "array", "items": {"type": "string"}, "description": "Players the user's team receives."},
                   "my_team": {"type": "string", "description": "Optional; defaults to the user's team."},
               }, ["other_team", "give", "get"]),
            fn("evaluate_three_team_trade", "Evaluate a trade between three teams, with the same simulation and odds as "
               "evaluate_trade for every team. List every player who changes teams and the team that gets him.", {
                   "moves": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["player", "to_team"],
                       "properties": {"player": {"type": "string", "description": "Player name."},
                                      "to_team": {"type": "string", "description": "The team that gets him (name or id)."}},
                   }},
               }, ["moves"]),
            fn("trade_ideas", "Search 1-for-1 and 2-for-2 trades that help a team and that the partner's projection also "
               "accepts.", {"team": team, "partner": {"type": "string", "description": "Optional partner team."},
                            "uneven": {"type": "boolean", "description": "Also search 2-for-1 and 1-for-2 packages. "
                                       "Only when the user asks for them."}}),
            fn("trade_offers", "What to offer for specific players on another team: every package of 1 to 3 of the user's "
               "players, simulated for both teams. Offers the other team's projection accepts, best for the user first; "
               "if none, the closest refusals.", {
                   "players": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3,
                               "description": "The players the user wants, all on one other team."},
                   "team": team,
                   "max_give": {"type": "integer", "minimum": 1, "maximum": 3,
                                "description": "Most players the user would send (default 3)."},
               }, ["players"]),
            fn("what_to_ask_for", "What to ask for 1 to 3 of the user's own players from a team that wants them (or from "
               "every team): every package of 1 to 3 of that team's players, simulated for both teams. Packages the other "
               "team's projection accepts, the most the user can ask for first; if none, the closest refusals.", {
                   "players": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3,
                               "description": "The user's players being shopped."},
                   "partner": {"type": "string", "description": "The team that wants them. Omit to search every team."},
                   "team": team,
                   "max_get": {"type": "integer", "minimum": 1, "maximum": 3,
                               "description": "Most players to ask for back. Omit for as many as the user gives; "
                                              "more than that only when the user asks for an uneven trade."},
               }, ["players"]),
            fn("three_team_trade_ideas", "Search three-team rotations: the user's team sends a player to team P, P sends one "
               "to team Q, and Q sends one back. Rotations that help the user and that both other teams' projections "
               "accept.", {"team": team, "partner": {"type": "string", "description": "Optional: only rotations that "
                                                                                      "include this team."}}),
            fn("simulate_matchup", "Monte Carlo simulation (10,000 runs) of a head-to-head week: win probabilities, score "
               "ranges, swing players and what happens if players sit.", {
                   "team_a": {"type": "string", "description": "Defaults to the user's team."},
                   "team_b": {"type": "string", "description": "Defaults to team A's scheduled opponent."},
                   "week": {"type": "integer", "description": "Matchup period; defaults to the current week."},
                   "sit": {"type": "array", "items": {"type": "string"}, "description": "Players to bench in the simulation."},
               }),
            fn("league_news", "Recent NBA headlines that mention players in this league, with their fantasy teams. Context "
               "only; projections do not use headlines."),
        ]

    def call(self, name: str, args: dict) -> Any:
        fn = self._funcs.get(name)
        if fn is None:
            raise ToolError(f"Unknown tool {name}")
        return fn(**args)

    # -- name resolution -----------------------------------------------------------------

    def team_id(self, ref: str | int | None, *, default_mine: bool = True) -> int:
        if ref in (None, ""):
            if default_mine and self.my_team_id is not None:
                return self.my_team_id
            raise ToolError("Which team? The user hasn't claimed a team, so pass a team name.")
        ids = {t.id: t for t in self.snap.teams}
        if str(ref).isdigit() and int(ref) in ids:
            return int(ref)
        low = str(ref).strip().lower()
        raw = [t.id for t in self.snap.teams if t.name.lower() == low or t.abbrev.lower() == low]
        if raw:
            return raw[0]
        want = normalize_name(str(ref))
        exact = [t.id for t in self.snap.teams if normalize_name(t.name) == want or normalize_name(t.abbrev) == want]
        if len(exact) == 1:
            return exact[0]
        partial = [t.id for t in self.snap.teams if want and (want in normalize_name(t.name)
                                                             or want in normalize_name(t.owner_name or ""))]
        if len(partial) == 1:
            return partial[0]
        raise ToolError(f"Couldn't identify team '{ref}'. Teams: " + ", ".join(t.name for t in self.snap.teams))

    def player_id(self, ref: str) -> str:
        if ref in self.snap.players:
            return ref
        players = list(self.snap.players.values())
        low = ref.strip().lower()
        raw = [p.id for p in players if p.name.lower() == low]
        if raw:
            return raw[0]
        want = normalize_name(ref)  # accent/suffix-insensitive ("Luka Doncic" finds "Luka Dončić")
        exact = [p for p in players if normalize_name(p.name) == want]
        if len(exact) == 1:
            return exact[0].id
        if len(exact) > 1:
            raise ToolError(f"'{ref}' is ambiguous: " + ", ".join(p.name for p in exact[:8]))
        partial = [p for p in players if want and want in normalize_name(p.name)]
        if len(partial) == 1:
            return partial[0].id
        if len(partial) > 1:
            raise ToolError(f"'{ref}' is ambiguous: " + ", ".join(p.name for p in partial[:8]))
        raise ToolError(f"No player named '{ref}' in this league's player pool.")

    def _name(self, pid: str) -> str:
        return self.snap.players[pid].name

    # -- tools -------------------------------------------------------------------------------

    def league_overview(self) -> dict:
        s = self.snap.settings
        teams = []
        for t in self.a.strength():
            teams.append({
                "rank": t.rank, "team": t.name, "team_id": t.team_id, "manager": t.owner,
                "is_users_team": t.team_id == self.my_team_id, "record": f"{t.wins}-{t.losses}-{t.ties}",
                "pts_per_week": _r(t.ppw, 0), "vs_league_avg": _r(t.ppw_vs_avg, 0), "power_score": _r(t.power_score, 3),
                "record_share_of_power_score": _r(t.record_weight, 2), "projected_all_play_win_rate": _r(t.projected_allplay, 3),
                "projected_record": f"{t.proj_wins:.0f}-{t.proj_losses:.0f}", "playoff_odds": _r(t.playoff_odds, 3),
                "title_odds": _r(t.title_odds, 3), "why": t.explanation.get("summary"),
            })
        return {"league": s.name, "scoring": s.scoring.kind, "current_week": self.snap.current_matchup_period,
                "playoff_teams": s.playoff_teams, "teams": teams}

    def team_roster(self, team: str | None = None) -> dict:
        tid = self.team_id(team)
        d = team_detail(self.a, tid)
        report = diagnose(self.ctx, tid, self.a.windows[tid], self.a.windows)
        roster = []
        for r in d["roster"]:
            row = _slim_player(r)
            row.update({"lineup_slot": r.get("slot"), "pts_per_week_for_team": _r(r.get("pts_per_week"), 0)})
            roster.append(row)
        return {
            "team": d["name"], "team_id": tid, "record": d["record"],
            "power_rank": d["strength"]["rank"] if d["strength"] else None,
            "summary": d["strength"]["explanation"]["summary"] if d["strength"] else None,
            "roster": roster,
            "weak_spots": [{"issue": w["title"], "detail": w["detail"]} for w in report[:5]],
            "upcoming_weeks": [{"week": u["matchup_period"], "projected": _r(u["proj_mean"], 0), "sd": _r(u["proj_sd"], 0),
                                "opponent_id": u["opponent_id"]} for u in d["upcoming"][:3]],
        }

    def free_agents(self, position: str | None = None, sort_by: str = "rest_of_season", limit: int = 12) -> dict:
        ids = [p.id for p in self.snap.free_agents() if not position or position in p.positions]
        rows = player_rows(self.a, ids)
        key = {"rest_of_season": "ros_value", "games_next_7_days": "games_next7", "fp_per_game": "fp_pg"}.get(sort_by, "ros_value")
        rows.sort(key=lambda r: -(r.get(key) or 0))
        return {"sorted_by": sort_by, "players": [_slim_player(r) for r in rows[: max(1, min(int(limit), 25))]]}

    def waiver_recommendations(self, team: str | None = None) -> dict:
        tid = self.team_id(team)
        moves = add_drop_moves(self.ctx, tid, self.a.windows[tid], limit=6)
        stream = streaming_moves(self.ctx, tid, limit=5)
        return {
            "team": self.snap.team(tid).name,
            "add_drop_rest_of_season": [{
                "add": m["add_name"], "drop": m["drop_name"], "pts_per_week_gain": _r(m["delta_per_week"]),
                "this_week_gain": _r(m["delta_this_week"]), "rest_of_season_gain": _r(m["delta_ros"], 0),
                "add_is_on_waivers": m["on_waivers"], "reasons": m["reasons"],
            } for m in moves],
            "streaming_this_week": {
                "days_left": stream["days_left"], "adds_allowed_per_matchup": stream.get("acquisition_limit"),
                "moves": [{"add": m["add_name"], "drop": m["drop_name"], "games_this_week": _r(m["games_this_week"]),
                           "this_week_gain": _r(m["delta_this_week"]), "rest_of_season_cost": _r(m["ros_cost"], 0)}
                          for m in stream["moves"]],
            },
        }

    def player_details(self, player: str) -> dict:
        pid = self.player_id(player)
        d = player_detail(self.a, pid)
        return {
            **_slim_player(d),
            "fantasy_team": self.snap.team(d["fantasy_team_id"]).name if d["fantasy_team_id"] else "free agent",
            "age": d.get("age"), "injury_note": d.get("injury_note"), "preseason_fp_per_game": _r(d["preseason_fp_pg"]),
            "availability_when_healthy": _r(d["availability"], 2),
            "projection_sources": [{"source": s["source"], "fp_per_game": _r(s["fp_pg"]), "weight": _r(s["weight"], 2)}
                                   for s in d["sources"]],
            "this_season_weight": _r(d["in_season_weight"], 2),
            "last_season_closing_games_weight": _r(d["carryover_weight"], 2),
            "adjustments": [{"what": x["label"], "detail": x["detail"]} for x in d["adjustments"]],
            "ml_factors_fp_per_game": d["explanation"][:5],
            "this_season_actual_vs_expected_per_game": d["xfp"],
            "projected_per_game": {k: _r(v) for k, v in d["per_game"].items()},
            "recent_games": [{k: g[k] for k in ("date", "team", "min", "fp", "PTS", "REB", "AST", "STL", "BLK", "TO")}
                             for g in d["game_log"][:5]],
            "seasons": [{k: s[k] for k in ("label", "team", "gp", "mpg", "fp_pg")} for s in d["seasons"][-3:]],
            "news": [n["headline"] for n in d["news"][:3]],
        }

    def evaluate_trade(self, other_team: str, give: list[str], get: list[str], my_team: str | None = None) -> dict:
        a = self.team_id(my_team)
        b = self.team_id(other_team, default_mine=False)
        give_ids = [self.player_id(p) for p in give]
        get_ids = [self.player_id(p) for p in get]
        try:
            ev = self.a.evaluator().evaluate(a, b, give_ids, get_ids)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return self._trade_result(ev)

    def evaluate_three_team_trade(self, moves: list[dict]) -> dict:
        sends: dict[int, dict[str, int]] = {}
        for m in moves:
            pid = self.player_id(m["player"])
            home = self.snap.players[pid].fantasy_team_id
            if home is None:
                raise ToolError(f"{self._name(pid)} is a free agent, not on a team.")
            to = self.team_id(m["to_team"], default_mine=False)
            sends.setdefault(home, {})[pid] = to
            sends.setdefault(to, {})
        if self.my_team_id in sends:  # the user's team first, so the other two count as the partners
            sends = {self.my_team_id: sends[self.my_team_id], **sends}
        if len(sends) != 3:
            raise ToolError(f"Players move between {len(sends)} teams; a three-team trade needs exactly three.")
        try:
            ev = self.a.evaluator().evaluate_multi(sends)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        out = self._trade_result(ev)
        out["moves"] = [{"player": self._name(m["player_id"]), "from": self.snap.team(m["from_team"]).name,
                         "to": self.snap.team(m["to_team"]).name} for m in ev["moves"]]
        return out

    def _trade_result(self, ev: dict) -> dict:
        sides = []
        for s in ev["sides"]:
            sides.append({
                "team": s["team_name"], "verdict": s["verdict"], "pts_per_week_change": _r(s["delta_per_week"]),
                "rest_of_season_change": _r(s["delta_ros"], 0), "rest_of_season_pct": _r(s["delta_pct"] * 100, 1),
                "playoff_weeks_change": _r(s["delta_playoffs"], 0),
                "playoff_odds": [_r(s["before_playoff_odds"], 2), _r(s["after_playoff_odds"], 2)],
                "title_odds": [_r(s["before_title_odds"], 2), _r(s["after_title_odds"], 2)],
                "auto_drops": [self._name(p) for p in s["auto_drops"]], "auto_adds": [self._name(p) for p in s["auto_adds"]],
                "open_spot_pickups_either_way": [self._name(p) for p in s["open_spot_adds"]],
                "valued_as_free_agent_pickup": [f"{self._name(p)} (as {self._name(fa)})" for p, fa in s["counted_as_pickups"]],
            })
        return {"sides": sides, "fairness": ev["fairness"], "summary": ev["summary"],
                "players": [{k: (_r(v) if isinstance(v, float) else v) for k, v in p.items()
                             if k in ("name", "fp_pg", "ros_games", "ros_points", "vorp", "injury_status", "games_out")}
                            for p in ev["players"]]}

    def trade_ideas(self, team: str | None = None, partner: str | None = None, uneven: bool = False) -> dict:
        tid = self.team_id(team)
        partners = [self.team_id(partner, default_mine=False)] if partner else None
        rows = find_trades(self.ctx, tid, self.a.evaluator(), 2, 2, partners, limit=6,
                           sizes=None if uneven else EVEN_SIZES)
        return {"team": self.snap.team(tid).name, "ideas": [{
            "partner": r["partner_name"], "give": [self._name(p) for p in r["give"]],
            "get": [self._name(p) for p in r["get"]], "my_pts_per_week_gain": _r(r["my_delta_per_week"]),
            "partner_change_pct": _r(r["partner_delta_pct"] * 100, 1), "fit": r["fairness"]["label"],
        } for r in rows]}

    def trade_offers(self, players: list[str], team: str | None = None, max_give: int = 3) -> dict:
        tid = self.team_id(team)
        counts = frozenset(range(1, max(1, min(int(max_give), 3)) + 1))
        try:
            r = find_offers(self.ctx, tid, [self.player_id(p) for p in players], self.a.evaluator(), counts, limit=6)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        offer = lambda o: {  # noqa: E731
            "give": [self._name(p) for p in o["give"]], "my_pts_per_week_gain": _r(o["my_delta_per_week"]),
            "partner_change_pct": _r(o["partner_delta_pct"] * 100, 1), "fit": o["fairness"]["label"],
        }
        return {"team": self.snap.team(tid).name, "partner": r["partner_name"], "get": [self._name(p) for p in r["get"]],
                "offers": [offer(o) for o in r["offers"]],
                "closest_refused_offers": [offer(o) for o in r["closest"]]}

    def what_to_ask_for(self, players: list[str], partner: str | None = None, team: str | None = None,
                        max_get: int | None = None) -> dict:
        tid = self.team_id(team)
        pt = self.team_id(partner, default_mine=False) if partner else None
        # Even by default, as trade_ideas: an uneven ask counts the free agent the partner picks up.
        counts = (frozenset({min(len(players), 3)}) if max_get is None
                  else frozenset(range(1, max(1, min(int(max_get), 3)) + 1)))
        try:
            r = find_asks(self.ctx, tid, [self.player_id(p) for p in players], pt, self.a.evaluator(), counts, limit=6)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        ask = lambda o: {  # noqa: E731
            "from_team": o["partner_name"], "get": [self._name(p) for p in o["get"]],
            "my_pts_per_week_gain": _r(o["my_delta_per_week"]), "partner_change_pct": _r(o["partner_delta_pct"] * 100, 1),
            "fit": o["fairness"]["label"],
        }
        return {"team": self.snap.team(tid).name, "give": [self._name(p) for p in r["give"]],
                "asks": [ask(o) for o in r["offers"]], "closest_refused_asks": [ask(o) for o in r["closest"]]}

    def three_team_trade_ideas(self, team: str | None = None, partner: str | None = None) -> dict:
        tid = self.team_id(team)
        pt = self.team_id(partner, default_mine=False) if partner else None
        if pt == tid:
            raise ToolError("Pick another team as the partner.")
        rows = find_three_team_trades(self.ctx, tid, self.a.evaluator(), [pt] if pt else (), limit=6)
        return {"team": self.snap.team(tid).name, "ideas": [{
            "moves": [{"player": self._name(m["player_id"]), "from": self.snap.team(m["from_team"]).name,
                       "to": self.snap.team(m["to_team"]).name} for m in r["moves"]],
            "my_pts_per_week_gain": _r(r["my_delta_per_week"]),
            "partners": [{"team": s["team_name"], "change_pct": _r(s["delta_pct"] * 100, 1)} for s in r["partners"]],
            "fit": r["fairness"]["label"],
        } for r in rows]}

    def simulate_matchup(self, team_a: str | None = None, team_b: str | None = None, week: int | None = None,
                         sit: list[str] | None = None) -> dict:
        a = self.team_id(team_a)
        mp = int(week or self.snap.current_matchup_period)
        if team_b:
            b = self.team_id(team_b, default_mine=False)
        else:
            m = next((x for x in self.snap.matchups if x.matchup_period == mp and a in (x.home_team_id, x.away_team_id)
                      and x.away_team_id is not None), None)
            if m is None:
                raise ToolError("No scheduled opponent that week; pass team_b.")
            b = m.away_team_id if m.home_team_id == a else m.home_team_id
        exclude = {self.player_id(p) for p in (sit or [])}
        try:
            r = MatchupSimulator(self.ctx).simulate(a, b, mp, exclude, n=6000)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        out = {"week": mp, "dates": [r["start"], r["end"]], "days_left": r["days_simulated"], "tie_prob": _r(r["tie_prob"], 3),
               "sitting": [self._name(p) for p in exclude], "teams": []}
        for t in r["teams"]:
            swing = sorted(t["players"], key=lambda p: -abs(p["swing"]))[:4]
            out["teams"].append({
                "team": t["name"], "win_prob": _r(t["win_prob"], 3), "projected": _r(t["mean"], 0),
                "range_80pct": [_r(t["p10"], 0), _r(t["p90"], 0)], "player_games_left": _r(t["games"]),
                "already_scored": t["accrued"],
                "swing_players": [{"name": p["name"], "games": _r(p["games"]), "expected_pts": _r(p["mean"], 0),
                                   "swing_pct": _r(p["swing"] * 100, 0)} for p in swing],
            })
        if "categories" in r:
            out["categories"] = r["categories"]["rows"]
        else:
            out["expected_margin"] = _r(r["margin"]["mean"], 0)
        return out

    def league_news(self) -> dict:
        rostered = {e.player_id: t.name for t in self.snap.teams for e in t.roster}
        items = []
        for n in self.snap.news[:15]:
            players = [f"{self.snap.players[p].name} ({rostered.get(p, 'free agent')})" for p in n.player_ids
                       if p in self.snap.players]
            items.append({"headline": n.headline, "published": n.published.isoformat() if n.published else None,
                          "players": players})
        return {"headlines": items}
