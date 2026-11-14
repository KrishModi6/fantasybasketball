"""NBA game predictions for the Overview page.

The predictions come from the game predictor in game-predictor/, which needs pandas, scikit-learn and XGBoost.
The desktop engine ships without those, so it doesn't run the predictor: a scheduled GitHub workflow does
(.github/workflows/game-predictions.yml) and publishes today's and tomorrow's games as one small JSON file,
which this module fetches, checks and keeps for a few minutes. FBB_GAMES_URL="" turns it off.

The file is someone else's bytes by the time it gets here, so only the fields the page shows are passed on,
each checked for its type. When the file can't be fetched, the last good copy is used.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from fbb.constants import ESPN_PRO_TEAMS
from fbb.history import CACHE_DIR

from ..config import get_settings

log = logging.getLogger(__name__)
TTL_S = 15 * 60
RETRY_S = 60  # after a failed fetch, how long before asking again
MAX_BYTES = 1_000_000
FORMAT = 1
_lock = threading.Lock()
_state: dict = {"at": None, "data": None}


def _text(x, limit: int = 80) -> str:
    return x[:limit] if isinstance(x, str) else ""


def _number(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


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


def predictions() -> dict:
    """Today's and tomorrow's NBA games with the predictor's odds: {"available": False} when there are none to show."""
    url = get_settings().games_url.strip()
    if not url:
        return {"available": False}
    with _lock:
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


def reset() -> None:
    """Forget what was fetched (tests)."""
    with _lock:
        _state.update(at=None, data=None)
