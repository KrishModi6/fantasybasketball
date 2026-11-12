"""Predict a game: win probabilities, a score, the factors behind them, and what changes with the lineup.

A game that hasn't been played has no box score to say who was in uniform, so each team's lineup is its
recent rotation (features.FeatureState.rotation) less the players ESPN's injury report lists as out. ``out``
and ``back`` change that by hand: sit a player, or put one back who is listed out or has been away.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from . import data
from .features import FEATURES, FeatureState, build, elo_probability, name_key
from .model import factors, predict
from .simulate import DEFAULT_SIMS, simulate

OUT_STATUSES = {"Out"}  # ESPN statuses that take a player out of the lineup; day-to-day players are assumed to play


def current_state(refresh: bool = False, progress=None) -> FeatureState:
    """Every team and player as of the latest game on record."""
    teams, players = data.load_logs(refresh=refresh, progress=progress)
    return build(teams, players)[1]


def _lineup(state: FeatureState, team_id: int, out: set[int], back: set[int], injuries: dict[str, str]) -> tuple[list[int], dict]:
    """Who is in uniform for a team, and a report of who is out and why. ``out`` and ``back`` are player ids."""
    rotation = state.rotation(team_id)
    roster = state.teams[team_id].roster
    names = {name_key(state.players[p].name): p for p in roster}
    sitting: dict[int, str] = {}
    for name, status in injuries.items():
        pid = names.get(name_key(name))
        if pid is not None and status in OUT_STATUSES:
            sitting[pid] = "injury report"
    for pid in out & set(roster):
        sitting[pid] = "what-if"
    returning = []
    for pid in back & set(roster):
        sitting.pop(pid, None)
        if pid not in rotation:
            returning.append(pid)
    playing = [p for p in rotation if p not in sitting] + returning
    show = lambda p: {"name": state.players[p].name, "value": round(state.players[p].value, 1)}  # noqa: E731
    report = {"playing": [show(p) for p in sorted(playing, key=lambda p: -state.players[p].value)[:9]],
              "out": [{**show(p), "why": why} for p, why in sorted(sitting.items(), key=lambda kv: -state.players[kv[0]].value)
                      if p in rotation],
              "questionable": sorted(n for n, s in injuries.items() if s not in OUT_STATUSES and name_key(n) in names
                                     and names[name_key(n)] in rotation)}
    return playing, report


def _find(state: FeatureState, name: str, teams: tuple[int, int]) -> int | None:
    """The player on one of the two teams with this name. A full name has to match (accents, punctuation and
    "Jr." aside); a single word is taken as a surname, and counts only if exactly one player on the two teams
    has it. Never a guess: "Jaylen Brown" must not sit another team's Brown."""
    key = name_key(name)
    pool = [p for t in teams for p in state.teams[t].roster]
    full = [p for p in pool if name_key(state.players[p].name) == key]
    if full:
        return max(full, key=lambda p: state.players[p].den)
    if key and " " not in key:
        surname = [p for p in pool if name_key(state.players[p].name).split(" ")[-1] == key]
        if len(surname) == 1:
            return surname[0]
    return None


def predict_game(bundle: dict, state: FeatureState, home: str, away: str, day: date | None = None, neutral: bool = False,
                 out: list[str] | None = None, back: list[str] | None = None, injuries: dict[str, str] | None = None,
                 sims: int = DEFAULT_SIMS, seed: int = 2027) -> dict:
    """Everything about one game. ``home`` and ``away`` are team abbreviations ("BOS")."""
    day = day or data.today()
    h, a = state.team_id(home), state.team_id(away)
    if h == a:
        raise ValueError("Pick two different teams.")
    out, back, injuries = list(out or []), list(back or []), dict(injuries or {})
    picked: tuple[set[int], set[int]] = (set(), set())
    for names, ids in ((out, picked[0]), (back, picked[1])):
        for name in names:
            pid = _find(state, name, (h, a))
            if pid is None and state.find_player(name) is not None:
                raise ValueError(f"{name} isn't on {state.teams[h].abbrev} or {state.teams[a].abbrev}.")
            if pid is None:
                raise ValueError(f"No player named {name!r} on {state.teams[h].abbrev} or {state.teams[a].abbrev}.")
            ids.add(pid)
    home_playing, home_report = _lineup(state, h, *picked, injuries)
    away_playing, away_report = _lineup(state, a, *picked, injuries)
    row = state.matchup(h, a, day, data.current_season(day), neutral, home_playing, away_playing)
    X = np.array([[row[f] for f in FEATURES]], dtype=float)
    pred = {k: float(v[0]) for k, v in predict(bundle["models"], X).items()}
    sim = simulate(pred["p"], pred["total"], bundle["sigma_margin"], bundle["sigma_total"], bundle["rho"], sims, seed)
    return {
        "date": day.isoformat(), "home": state.teams[h].abbrev, "away": state.teams[a].abbrev, "neutral": neutral,
        "home_win_probability": pred["p"], "away_win_probability": 1 - pred["p"],
        "models": {"logistic": pred["lr"], "xgboost": pred["xgb"],
                   "elo": float(elo_probability(row["elo_diff"], row["neutral"]))},
        "predicted_score": {"home": round(sim["home_score"]), "away": round(sim["away_score"])},
        "model_margin": pred["margin"], "model_total": pred["total"],
        "simulation": sim, "factors": factors(bundle["models"], row),
        "lineups": {"home": home_report, "away": away_report},
        "features": row,
    }


def predict_day(bundle: dict, state: FeatureState, day: date, schedule=None, injuries: dict[str, str] | None = None,
                sims: int = DEFAULT_SIMS) -> list[dict]:
    """Every game on the schedule for ``day`` that both teams are known for."""
    games = data.load_schedule(day) if schedule is None else schedule[schedule["GAME_DATE"] == day.isoformat()]
    out = []
    for g in games.to_dict("records"):
        if g["HOME"] not in state.by_abbrev or g["AWAY"] not in state.by_abbrev:
            continue
        out.append({**predict_game(bundle, state, g["HOME"], g["AWAY"], day, bool(g.get("NEUTRAL")), injuries=injuries,
                                   sims=sims), "status": g.get("STATUS", "scheduled"), "tip": g.get("TIP")})
    return out
