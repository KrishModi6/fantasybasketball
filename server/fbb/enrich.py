"""Attach multi-season history, ages and debut years to snapshot players.

Live league feeds only carry the current and previous season, but the SPS
baseline and ML features want three seasons plus age. This fills the gaps from
the local history dataset and an on-disk bio cache.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .history import CACHE_DIR, HISTORY_DIR, load_history
from .models import Player

log = logging.getLogger(__name__)
_BIO_LOCK = threading.Lock()
BIO_BUNDLED = HISTORY_DIR / "athletes.json"  # shipped with the training data
BIO_EXTRA = CACHE_DIR / "athletes_extra.json"  # players first seen at runtime


def _read(path: Path) -> dict[str, dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _load_bios() -> dict[str, dict]:
    return {**_read(BIO_BUNDLED), **_read(BIO_EXTRA)}


def fetch_missing_bios(player_ids: list[str], max_fetch: int = 120) -> int:
    """Fetch ages for players we have never seen (rookies, call-ups). Best effort."""
    from .connectors.espn import EspnClient

    with _BIO_LOCK:
        bios = _load_bios()
        todo = [pid for pid in player_ids if pid not in bios][:max_fetch]
        if not todo:
            return 0
        client = EspnClient(timeout=15)
        try:
            for pid in todo:
                try:
                    a = client.athlete(pid).get("athlete", {})
                    bios[pid] = {"age": a.get("age"), "debut_year": a.get("debutYear")}
                except Exception as exc:  # noqa: BLE001 - a missing bio must never break a sync
                    bios[pid] = {"age": None, "debut_year": None, "error": str(exc)[:60]}
        finally:
            client.close()
        extra = {**_read(BIO_EXTRA), **{pid: bios[pid] for pid in todo}}
        BIO_EXTRA.parent.mkdir(parents=True, exist_ok=True)
        BIO_EXTRA.write_text(json.dumps(extra))
        return len(todo)


def enrich_players(players: dict[str, Player], season: int) -> None:
    ds = load_history()
    bios = _load_bios()
    for pid, p in players.items():
        known = {h.season for h in p.history}
        extra = [h for h in ds.lines.get(pid, []) if h.season < season and h.season not in known]
        if extra:
            p.history = sorted(p.history + extra, key=lambda h: h.season)
        bio = bios.get(pid) or {}
        if p.age is None and bio.get("age") is not None:
            # Bios record age "now" (start of the 2026-27 season); shift to the projected season.
            p.age = float(bio["age"]) - (ds.age_season - season)
        if p.debut_year is None and bio.get("debut_year"):
            p.debut_year = int(bio["debut_year"])
