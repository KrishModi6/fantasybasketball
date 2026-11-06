"""Gets the league admin's rulings to everyone else's desktop app.

Each desktop app has its own database, so a ruling saved on the admin's computer reaches nobody by itself. So
rulings travel through the Cloudflare Worker that already serves the AI assistant: the admin's copy sends them,
and every other copy reads them into its own trade_rulings table, where the board and feed pick them up.

The client token baked into release builds identifies the app, not a person, so it can't say who the admin is.
A random key that only the admin's copy holds does that. The first copy to publish a ruling claims the league with
that key, and from then on the Worker refuses writes without it. Copies that joined with an invite code never
send anything, and a copy someone connected by hand finds out from the Worker that the league already has an admin.

A shared server needs none of this: every member reads the same database. ESPN leagues only, since their league
id is what lets separate copies recognize each other.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..orm import Activity, League, TradeRuling, UserSettings
from ..realtime import hub
from ..security import decrypt_json, encrypt_json
from .rulings import iso, league_rulings, ruling_message

log = logging.getLogger(__name__)
TIMEOUT_S = 5.0
PULL_EVERY_S = 60.0
_last_pull: dict[str, float] = {}


class Refused(Exception):
    """The Worker says someone else is this league's admin."""


class Unreachable(Exception):
    """The Worker couldn't be reached, or didn't answer properly."""


def enabled() -> bool:
    s = get_settings()
    return s.local_mode and bool(s.ai_proxy_url and s.ai_proxy_token)


def league_key(league: League) -> str | None:
    """The same for every copy of a league in a season, without saying which league it is."""
    if league.source != "espn":
        return None
    league_id = decrypt_json(league.connector_config).get("league_id")
    return hashlib.sha256(f"courtside:espn:{league_id}:{league.season}".encode()).hexdigest()[:40] if league_id else None


def reach(league: League) -> str:
    """Who a ruling in this league reaches: its members on a shared server, or everyone's apps, or only this one."""
    if not get_settings().local_mode:
        return "members"
    return "league" if enabled() and league_key(league) else "this_copy"


def _stored_key(db: Session, user_id: str) -> str | None:
    row = db.get(UserSettings, user_id)
    return decrypt_json(row.data).get("rulings_admin_key") if row else None


def ensure_admin_key(db: Session, user_id: str) -> None:
    """Saved before anything is sent: a league claimed with a key this copy then lost would be locked for good."""
    if _stored_key(db, user_id):
        return
    row = db.get(UserSettings, user_id) or UserSettings(user_id=user_id)
    data = decrypt_json(row.data)
    data["rulings_admin_key"] = secrets.token_urlsafe(32)
    row.data = encrypt_json(data)
    db.add(row)
    db.commit()


def _call(method: str, path: str, admin_key: str | None = None, body: dict | None = None) -> httpx.Response | None:
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.ai_proxy_token}"}
    if admin_key:
        headers["X-League-Admin"] = admin_key
    try:
        return httpx.request(method, s.ai_proxy_url.rstrip("/") + path, headers=headers, json=body, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        log.info("rulings: couldn't reach the Worker: %s", exc)
        return None


def _send(db: Session, league_path: str, user_id: str, row: TradeRuling) -> None:
    """Leaves the ruling pending if the Worker can't take it now; the next pull tries again."""
    r = _call("PUT", f"{league_path}/{row.trade_key}", _stored_key(db, user_id),
              {"ruling": row.ruling, "note": row.note, "trade": row.trade, "numbers": row.numbers,
               "decided_at": iso(row.decided_at)})
    if r is None:
        return
    if r.status_code == 403:
        raise Refused
    if r.is_success:
        row.published, row.pending = True, False
    else:
        log.warning("rulings: the Worker answered %s", r.status_code)


def publish(db: Session, league: League, user_id: str, row: TradeRuling) -> None:
    """Sends a ruling to the rest of the league, if it can reach them. Raises Refused if this isn't the admin's copy."""
    key = league_key(league)
    if not enabled() or key is None:
        return
    row.pending = True
    _send(db, f"/v1/leagues/{key}/rulings", user_id, row)


def unpublish(db: Session, league: League, user_id: str, row: TradeRuling) -> None:
    """Takes a ruling back from the rest of the league. It can't quietly wait: they'd keep seeing it."""
    key = league_key(league)
    if not enabled() or key is None or not row.published:
        return
    r = _call("DELETE", f"/v1/leagues/{key}/rulings/{row.trade_key}", _stored_key(db, user_id))
    if r is None or (not r.is_success and r.status_code != 403):
        raise Unreachable
    if r.status_code == 403:
        raise Refused


def note_admin_elsewhere(league: League, elsewhere: bool) -> None:
    cfg = decrypt_json(league.connector_config)
    if bool(cfg.get("admin_elsewhere")) == elsewhere:
        return
    if elsewhere:
        cfg["admin_elsewhere"] = True
    else:
        cfg.pop("admin_elsewhere", None)
    league.connector_config = encrypt_json(cfg)


def _mirror(db: Session, league: League, rulings: list[dict]) -> bool:
    """Makes this copy's rulings match the admin's, and puts what changed in the league feed."""
    local = {r.trade_key: r for r in league_rulings(db, league.id)}
    changed = False
    for item in rulings:
        row = local.get(item["key"])
        fresh = (item["ruling"], item.get("note", ""), item.get("trade"), item.get("numbers"), item["decided_at"])
        if row is not None and (row.ruling, row.note, row.trade, row.numbers, iso(row.decided_at)) == fresh:
            continue
        changed = True
        previous = row.ruling if row is not None else None
        if row is None:
            row = TradeRuling(league_id=league.id, trade_key=item["key"], decided_by=league.owner_id)
            db.add(row)
        row.ruling, row.note, row.trade, row.numbers = item["ruling"], item.get("note", ""), item["trade"], item.get("numbers")
        row.decided_at = datetime.fromisoformat(item["decided_at"])
        row.published, row.pending = True, False
        if previous != row.ruling:
            db.add(Activity(league_id=league.id, kind="ruling", message=ruling_message(row, previous),
                            payload={"key": row.trade_key, "ruling": row.ruling}))
    wanted = {item["key"] for item in rulings}
    for key, row in local.items():
        if key not in wanted:
            changed = True
            db.add(Activity(league_id=league.id, kind="ruling", payload={"key": key},
                            message=f"League admin withdrew a ruling: {row.trade.get('text', '')}"[:500]))
            db.delete(row)
    return changed


def pull(db: Session, league: League) -> bool:
    """Brings this copy into line with the league: sends what the admin's copy owes, reads what every other copy lacks.

    Returns whether this copy's rulings changed.
    """
    key = league_key(league)
    if not enabled() or key is None:
        return False
    r = _call("GET", f"/v1/leagues/{key}/rulings", _stored_key(db, league.owner_id))
    if r is None or not r.is_success:
        return False
    data = r.json()
    cfg = decrypt_json(league.connector_config)
    if data["you"] or not (data["claimed"] or cfg.get("shared_by") or cfg.get("joined_with_code")):
        note_admin_elsewhere(league, False)
        path = f"/v1/leagues/{key}/rulings"
        try:
            for row in league_rulings(db, league.id):
                if row.pending:
                    _send(db, path, league.owner_id, row)
        except Refused:
            note_admin_elsewhere(league, True)
        db.commit()
        return False
    note_admin_elsewhere(league, bool(data["claimed"]))
    changed = _mirror(db, league, data["rulings"])
    db.commit()
    if changed:
        hub.publish(league.id, {"type": "trade_ruled"})
    return changed


def pull_if_due(db: Session, league: League) -> None:
    """For page loads and refreshes: at most once a minute per league, and never a reason for them to fail."""
    if not enabled():
        return
    now = time.monotonic()
    if now - _last_pull.get(league.id, -math.inf) < PULL_EVERY_S:
        return
    _last_pull[league.id] = now
    try:
        pull(db, league)
    except Exception:  # noqa: BLE001 - rulings arriving late must never break the page or refresh that asked
        log.exception("rulings: pull failed for %s", league.id)
        db.rollback()
