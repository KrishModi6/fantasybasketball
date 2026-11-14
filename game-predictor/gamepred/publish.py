"""The day's predictions as one small file, for programs that only read them.

Courtside's desktop app can't run this predictor itself (it ships without pandas, scikit-learn and XGBoost), so
a scheduled job runs ``python -m gamepred publish`` and puts the file where every copy of the app can fetch it
(.github/workflows/game-predictions.yml). The file holds today's and tomorrow's games on the NBA's calendar.

A game is predicted again on every run until it tips off. From then on it keeps the prediction that was published
before it started, so the file never shows a "prediction" made with the game's own result in the data.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import data
from .features import FeatureState
from .predict import predict_game

FORMAT = 1
TOP_FACTORS = 4


def game_entry(bundle: dict, state: FeatureState, game: dict, day: date, injuries: dict[str, str], sims: int) -> dict:
    """One scheduled game, predicted, in the published shape."""
    g = predict_game(bundle, state, game["HOME"], game["AWAY"], day, bool(game.get("NEUTRAL")), injuries=injuries, sims=sims)
    sim = g["simulation"]
    out = lambda side: [p["name"] for p in g["lineups"][side]["out"]]  # noqa: E731
    return {
        "id": str(game["GAME_ID"]), "tip": game.get("TIP"), "status": game.get("STATUS", "scheduled"),
        "neutral": bool(game.get("NEUTRAL")),
        # The score so far, once a game has tipped off (the prediction beside it is still the one from before it).
        "home_pts": game.get("HOME_PTS"), "away_pts": game.get("AWAY_PTS"),
        "home": g["home"], "away": g["away"], "home_id": int(game["HOME_ID"]), "away_id": int(game["AWAY_ID"]),
        "home_win": round(g["home_win_probability"], 4),
        "home_score": g["predicted_score"]["home"], "away_score": g["predicted_score"]["away"],
        "home_range": [round(x) for x in sim["home_range"]], "away_range": [round(x) for x in sim["away_range"]],
        "factors": [{"factor": f["factor"], "points": round(f["points"], 1)} for f in g["factors"][:TOP_FACTORS]],
        "home_out": out("home"), "away_out": out("away"),
        "home_questionable": g["lineups"]["home"]["questionable"], "away_questionable": g["lineups"]["away"]["questionable"],
    }


def earlier_games(previous: dict | None) -> dict[str, dict]:
    """The games in the file published last time, by id. Anything that isn't that file gives none."""
    try:
        if previous["format"] != FORMAT:
            return {}
        return {str(g["id"]): g for d in previous["days"] for g in d["games"] if "home_win" in g}
    except (KeyError, TypeError):
        return {}


def started_entry(game: dict, earlier: dict | None) -> dict | None:
    """A game that has tipped off: the prediction published before it did, with the score so far."""
    if not earlier:
        return None
    return {**earlier, "status": game.get("STATUS", "live"), "home_pts": game.get("HOME_PTS"), "away_pts": game.get("AWAY_PTS")}


def build(bundle: dict, state: FeatureState, first: date | None = None, days: int = 2, schedule=None,
          injuries: dict[str, str] | None = None, sims: int = 10_000, now: datetime | None = None,
          previous: dict | None = None) -> dict:
    """Predictions for ``days`` days from ``first`` (today on the NBA's calendar). ``schedule(day)`` lists a day's
    games (ESPN's scoreboard by default). ``previous`` is the file published last time: a game that has started
    keeps its prediction from there. One that has started with no earlier prediction is predicted now, unless the
    results in ``state`` already reach its day (it could be among them), in which case it is left out."""
    first = first or data.today()
    schedule = schedule or data.scoreboard
    injuries = data.load_injuries() if injuries is None else injuries
    bt = bundle["metrics"]["backtest"]
    earlier = earlier_games(previous)
    out_days = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        games = []
        for g in schedule(day):
            if g["HOME"] not in state.by_abbrev or g["AWAY"] not in state.by_abbrev:
                continue
            if g.get("STATUS", "scheduled") != "scheduled":
                kept = started_entry(g, earlier.get(str(g["GAME_ID"])))
                if kept or (state.last_day is not None and state.last_day >= day):
                    games += [kept] if kept else []
                    continue
            games.append(game_entry(bundle, state, g, day, injuries, sims))
        out_days.append({"date": day.isoformat(), "games": games})
    return {
        "format": FORMAT,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "data_through": str(state.last_day), "trained_through": bundle["metrics"]["trained_through"],
        "injuries": bool(injuries),
        "backtest": {"games": bt["games"], "accuracy": round(bt["overall"]["blend"]["accuracy"], 4),
                     "elo_accuracy": round(bt["overall"]["elo"]["accuracy"], 4),
                     "home_accuracy": round(bt["overall"]["home"]["accuracy"], 4)},
        "days": out_days,
    }


def read(path: Path | None) -> dict | None:
    """The file published last time, or None when there isn't one that can be read."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None


def write(path: Path, predictions: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(predictions, separators=(",", ":")), encoding="utf-8")
