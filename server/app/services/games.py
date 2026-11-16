"""NBA game predictions for the Overview page.

The predictions come from the game predictor in game-predictor/, which needs pandas, scikit-learn and XGBoost.
The desktop engine ships without those, so it doesn't run the predictor: a scheduled GitHub workflow does
(.github/workflows/game-predictions.yml) and publishes today's and tomorrow's games as one small JSON file,
which this module fetches, checks and keeps for a few minutes. FBB_GAMES_URL="" turns it off.

The file is someone else's bytes by the time it gets here, so only the fields the page shows are passed on,
each checked for its type. When the file can't be fetched, the last good copy is used.

The file is only published a few times a day, so on its own a game's score would be hours old. Once a game in it
has tipped off and isn't over, this module also reads ESPN's scoreboard for that day (FBB_GAMES_SCORES_URL="" turns
that off), kept for LIVE_TTL_S, and lays the score, period and clock over the published game. From those and the
pregame odds it works out a live chance to win (live_chance), so the odds move with the game. The pregame
prediction itself never changes once a game has started.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from statistics import NormalDist

import httpx

from fbb.constants import ESPN_PRO_TEAMS
from fbb.history import CACHE_DIR

from ..config import get_settings

log = logging.getLogger(__name__)
TTL_S = 15 * 60
RETRY_S = 60  # after a failed fetch, how long before asking again
LIVE_TTL_S = 20  # how long a day's live scores are kept while its games are on
MAX_BYTES = 1_000_000
MAX_BOARD_BYTES = 4_000_000  # ESPN's scoreboard is about 30 KB a game
FORMAT = 1
# How far a final margin lands from its prediction: one standard deviation, in points (gamepred/simulate.py).
MARGIN_SD = 14.0
GAME_S = 48 * 60
GAME_WINDOW = timedelta(hours=6)
_lock = threading.Lock()
_state: dict = {"at": None, "data": None}
_boards: dict[str, dict] = {}  # date -> {"at": when to ask again, "games": {game id: live fields}}


def _text(x, limit: int = 80) -> str:
    return x[:limit] if isinstance(x, str) else ""


def _number(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _names(x) -> list[str]:
    return [_text(n) for n in x[:15] if isinstance(n, str)] if isinstance(x, list) else []


def _range(x) -> list[float] | None:
    return [float(x[0]), float(x[1])] if isinstance(x, list) and len(x) == 2 and all(_number(v) is not None for v in x) else None


def _team(team_id, fallback) -> str:
    """Courtside's abbreviation for an NBA team (ESPN's fantasy and scoreboard feeds share team ids)."""
    return ESPN_PRO_TEAMS.get(team_id, "") if isinstance(team_id, int) and team_id in ESPN_PRO_TEAMS else _text(fallback, 6)


def _game(g: dict) -> dict | None:
    p = _number(g.get("home_win"))
    home, away = _team(g.get("home_id"), g.get("home")), _team(g.get("away_id"), g.get("away"))
    if p is None or not 0 <= p <= 1 or not home or not away:
        return None
    status = g.get("status") if g.get("status") in ("scheduled", "live", "final") else "scheduled"
    factors = [{"factor": _text(f.get("factor")), "points": _number(f.get("points"))}
               for f in (g.get("factors") if isinstance(g.get("factors"), list) else [])[:6]
               if isinstance(f, dict) and _text(f.get("factor")) and _number(f.get("points")) is not None]
    return {
        "id": _text(str(g.get("id") or ""), 24), "tip": _text(g.get("tip"), 40) or None, "status": status,
        "neutral": g.get("neutral") is True, "home": home, "away": away, "home_win": p,
        "home_score": _number(g.get("home_score")), "away_score": _number(g.get("away_score")),
        "home_range": _range(g.get("home_range")), "away_range": _range(g.get("away_range")),
        "home_pts": _number(g.get("home_pts")), "away_pts": _number(g.get("away_pts")),
        # Filled in from ESPN's scoreboard while the game is on (see _with_live).
        "period": None, "clock": None, "live_home_win": None,
        "factors": factors, "home_out": _names(g.get("home_out")), "away_out": _names(g.get("away_out")),
        "home_questionable": _names(g.get("home_questionable")), "away_questionable": _names(g.get("away_questionable")),
    }


def clean(raw) -> dict | None:
    """The published file as the page gets it, or None if it isn't one this version understands."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT or not isinstance(raw.get("days"), list):
        return None
    days = []
    for d in raw["days"][:7]:
        if not isinstance(d, dict) or not isinstance(d.get("date"), str) or not isinstance(d.get("games"), list):
            continue
        games = [g for g in (_game(x) for x in d["games"][:30] if isinstance(x, dict)) if g is not None]
        days.append({"date": d["date"][:10], "games": games})
    bt = raw.get("backtest") if isinstance(raw.get("backtest"), dict) else {}
    return {
        "available": True, "generated_at": _text(raw.get("generated_at"), 40) or None,
        "data_through": _text(raw.get("data_through"), 10) or None, "injuries": raw.get("injuries") is True,
        "backtest": {k: _number(bt.get(k)) for k in ("games", "accuracy", "elo_accuracy", "home_accuracy")},
        "days": days,
    }


def _fetch(url: str) -> dict | None:
    try:
        r = httpx.get(url, timeout=10.0, follow_redirects=True, headers={"Accept": "application/json"})
        r.raise_for_status()
        if len(r.content) > MAX_BYTES:
            raise ValueError("the predictions file is too large")
        return clean(json.loads(r.content))
    except (httpx.HTTPError, ValueError) as exc:
        log.info("game predictions unavailable: %s", exc)
        return None


def _saved() -> dict | None:
    try:
        return clean(json.loads((CACHE_DIR / "games.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _published() -> dict:
    """The published file, fetched at most every TTL_S (call with _lock held)."""
    url = get_settings().games_url.strip()
    if not url:
        return {"available": False}
    now = time.monotonic()
    if _state["at"] is not None and now < _state["at"]:
        return _state["data"] or {"available": False}
    fresh = _fetch(url)
    if fresh is not None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / "games.json").write_text(json.dumps({**fresh, "format": FORMAT}), encoding="utf-8")
        except OSError:
            pass  # the copy on disk is only for the next time GitHub can't be reached
        _state.update(at=now + TTL_S, data=fresh)
    else:
        _state.update(at=now + RETRY_S, data=_state["data"] or _saved())
    return _state["data"] or {"available": False}


# ---- Live scores ------------------------------------------------------------------------------------------


def live_chance(home_win: float, home_pts: float, away_pts: float, seconds_left: float) -> float:
    """The home team's chance to win from here. The final margin is taken to be the lead now, plus the share of
    the pregame expected margin still to come, give or take MARGIN_SD x sqrt(share of the game left). The pregame
    expected margin is the one that makes the pregame chance come out (as in gamepred/simulate.py). At tip-off this
    is the pregame chance, and it goes to 0 or 1 as the clock runs out."""
    lead = home_pts - away_pts
    share = max(seconds_left, 0.0) / GAME_S
    if share <= 0:
        return 1.0 if lead > 0 else 0.0 if lead < 0 else 0.5  # level at the buzzer: overtime, about a coin flip
    expected = MARGIN_SD * NormalDist().inv_cdf(min(max(home_win, 1e-4), 1 - 1e-4))
    return NormalDist().cdf((lead + expected * share) / (MARGIN_SD * math.sqrt(share)))


def _period_name(period: int) -> str:
    return f"Q{period}" if period <= 4 else "OT" if period == 5 else f"{period - 4}OT"


def _live_game(e) -> tuple[str, dict] | None:
    """One game on ESPN's scoreboard as (id, the fields laid over the published game), or None if it can't be read."""
    if not isinstance(e, dict) or not isinstance(e.get("id"), (str, int)) or isinstance(e.get("id"), bool):
        return None
    comps = e.get("competitions")
    comp = comps[0] if isinstance(comps, list) and comps and isinstance(comps[0], dict) else {}
    competitors = comp.get("competitors") if isinstance(comp.get("competitors"), list) else []
    sides = {c.get("homeAway"): c for c in competitors if isinstance(c, dict)}
    status = e.get("status") if isinstance(e.get("status"), dict) else {}
    kind = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = {"pre": "scheduled", "in": "live", "post": "final"}.get(kind.get("state"))
    if kind.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"):
        return None  # "over" with a score of 0-0: the published game is left as it is
    if state is None or set(sides) != {"home", "away"}:
        return None
    out: dict = {"status": state, "period": None, "clock": None, "seconds_left": None}
    if state != "scheduled":
        points = {k: c.get("score") for k, c in sides.items()}
        if not all(isinstance(v, str) and v.isdigit() and len(v) <= 3 for v in points.values()):
            return None
        out.update(home_pts=float(points["home"]), away_pts=float(points["away"]))
    period, clock = status.get("period"), _number(status.get("clock"))
    if state == "live" and isinstance(period, int) and not isinstance(period, bool) and 1 <= period <= 20 and clock is not None:
        clock = min(max(clock, 0.0), 720.0 if period <= 4 else 300.0)
        out["period"] = period
        out["seconds_left"] = (4 - period) * 720 + clock if period <= 4 else clock
        if clock > 0:
            shown = _text(status.get("displayClock"), 8) or f"{int(clock // 60)}:{int(clock % 60):02d}"
            out["clock"] = f"{_period_name(period)} {shown}"
        else:
            out["clock"] = "Halftime" if period == 2 else f"End of {_period_name(period)}"
    return str(e["id"])[:24], out


def _scoreboard(url: str, day: str) -> dict[str, dict] | None:
    """ESPN's games on ``day`` (YYYY-MM-DD on the NBA's calendar) by id, or None when the scoreboard can't be read."""
    try:
        r = httpx.get(url, params={"dates": day.replace("-", ""), "limit": 100}, timeout=8.0, follow_redirects=True,
                      headers={"Accept": "application/json"})
        r.raise_for_status()
        if len(r.content) > MAX_BOARD_BYTES:
            raise ValueError("the scoreboard is too large")
        raw = json.loads(r.content)
        events = raw.get("events") if isinstance(raw, dict) else None
        if not isinstance(events, list):
            raise ValueError("the scoreboard lists no games")
        return dict(g for g in (_live_game(e) for e in events[:60]) if g is not None)
    except (httpx.HTTPError, ValueError) as exc:
        log.info("live NBA scores unavailable: %s", exc)
        return None


def _tipped(g: dict, now: datetime) -> bool:
    """Whether the game's tip-off time has come, in the last GAME_WINDOW: a game that tipped off longer ago than
    that is over (or was postponed) whatever the file says, so its day's scoreboard isn't read for it."""
    try:
        tip = datetime.fromisoformat(g["tip"].replace("Z", "+00:00")) if g.get("tip") else None
    except ValueError:
        return False
    return tip is not None and tip.tzinfo is not None and tip <= now < tip + GAME_WINDOW


def _overlay(g: dict, live: dict | None) -> dict:
    """A published game with ESPN's live fields over it. A game the file already has as final stays as it is."""
    if live is None or g["status"] == "final":
        return g
    out = {**g, **{k: v for k, v in live.items() if k != "seconds_left"}}
    if live["status"] == "live" and live["seconds_left"] is not None:
        out["live_home_win"] = round(live_chance(g["home_win"], live["home_pts"], live["away_pts"], live["seconds_left"]), 4)
    return out


def _with_live(data: dict, now: datetime) -> dict:
    """``data`` with ESPN's live scores laid over every game that has tipped off and isn't final yet (call with
    _lock held). A day's scoreboard is only read while one of its games is on."""
    url = get_settings().games_scores_url.strip()
    if not url or not data.get("available"):
        return data
    days, mono = [], time.monotonic()
    for d in data["days"]:
        games = d["games"]
        if any(g["status"] == "live" or (g["status"] == "scheduled" and _tipped(g, now)) for g in games):
            board = _boards.get(d["date"])
            if board is None or mono >= board["at"]:
                fresh = _scoreboard(url, d["date"])
                if fresh is None:
                    wait, fresh = RETRY_S, board["games"] if board else {}
                elif all(fresh.get(g["id"], {}).get("status") == "final" for g in games):
                    wait = TTL_S  # all over: nothing will change until the next file says so
                else:
                    wait = LIVE_TTL_S
                board = {"at": mono + wait, "games": fresh}
                _boards[d["date"]] = board
            games = [_overlay(g, board["games"].get(g["id"])) for g in games]
        days.append({**d, "games": games})
    return {**data, "days": days}


def predictions(now: datetime | None = None) -> dict:
    """Today's and tomorrow's NBA games with the predictor's odds, and live scores for the games that are on:
    {"available": False} when there are none to show."""
    with _lock:
        return _with_live(_published(), now or datetime.now(timezone.utc))


def reset() -> None:
    """Forget what was fetched (tests)."""
    with _lock:
        _state.update(at=None, data=None)
        _boards.clear()
