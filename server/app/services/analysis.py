"""Builds and caches the analysis for a league's latest snapshot.

Building projections + rest-of-season lineup windows takes ~1-2 s, so results
are cached per (league, snapshot id, overrides version) and rebuilt only when
new data arrives or a member changes an override.
"""

from __future__ import annotations

import logging
import threading
import zlib
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fbb.context import AnalysisContext
from fbb.enrich import enrich_players
from fbb.ml.model import MODEL_PATH, ProjectionModel
from fbb.models import LeagueSnapshot, PlayerOverride
from fbb.projections import ProjectionEngine
from fbb.strength import TeamStrength, league_strength, team_windows
from fbb.trades import TradeEvaluator

from ..orm import League, PlayerOverrideRow, Snapshot

log = logging.getLogger(__name__)
CACHE_SIZE = 12


class ModelHolder:
    """Loads the projection model once and reloads it when the file changes (after retraining)."""

    def __init__(self, path: Path = MODEL_PATH):
        self.path = path
        self._model: ProjectionModel | None = None
        self._mtime: float | None = None
        self._lock = threading.Lock()

    def get(self) -> ProjectionModel | None:
        with self._lock:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
            if mtime != self._mtime:
                self._model = ProjectionModel.load(self.path)
                self._mtime = mtime
            return self._model


models = ModelHolder()


def pack_snapshot(snap: LeagueSnapshot) -> bytes:
    return zlib.compress(snap.model_dump_json().encode(), 6)


def unpack_snapshot(blob: bytes) -> LeagueSnapshot:
    return LeagueSnapshot.model_validate_json(zlib.decompress(blob))


class LeagueAnalysis:
    def __init__(self, league_id: str, snapshot_id: int, snapshot: LeagueSnapshot):
        self.league_id = league_id
        self.snapshot_id = snapshot_id
        self.snapshot = snapshot
        self.engine = ProjectionEngine(snapshot, models.get())
        self.proj = self.engine.project_all()
        self.ctx = AnalysisContext(snapshot, self.proj)
        self.windows = team_windows(self.ctx)
        self.built_at = datetime.now(timezone.utc)
        self._strength: list[TeamStrength] | None = None
        self._evaluator: TradeEvaluator | None = None
        self._lock = threading.Lock()

    def strength(self) -> list[TeamStrength]:
        with self._lock:
            if self._strength is None:
                self._strength = league_strength(self.ctx, self.windows)
            return self._strength

    def evaluator(self) -> TradeEvaluator:
        with self._lock:
            if self._evaluator is None:
                self._evaluator = TradeEvaluator(self.ctx, self.windows)
            return self._evaluator


_cache: "OrderedDict[tuple, LeagueAnalysis]" = OrderedDict()
_cache_lock = threading.Lock()
_build_locks: dict[str, threading.Lock] = {}


def latest_snapshot_row(db: Session, league_id: str) -> Snapshot | None:
    return db.scalar(select(Snapshot).where(Snapshot.league_id == league_id)
                     .order_by(Snapshot.created_at.desc(), Snapshot.id.desc()).limit(1))


def _overrides(db: Session, league_id: str) -> tuple[list[PlayerOverride], str]:
    rows = db.scalars(select(PlayerOverrideRow).where(PlayerOverrideRow.league_id == league_id)).all()
    version = f"{len(rows)}:{max((r.updated_at for r in rows), default='')}"
    return [PlayerOverride(player_id=r.player_id, minutes_pct=r.minutes_pct, out_games=r.out_games, note=r.note)
            for r in rows], version


def get_analysis(db: Session, league: League) -> LeagueAnalysis | None:
    row = latest_snapshot_row(db, league.id)
    if row is None:
        return None
    overrides, version = _overrides(db, league.id)
    model = models.get()
    key = (league.id, row.id, version, id(model))
    with _cache_lock:
        hit = _cache.get(key)
        if hit:
            _cache.move_to_end(key)
            return hit
        lock = _build_locks.setdefault(league.id, threading.Lock())
    with lock:  # one build per league at a time; others wait and reuse it
        with _cache_lock:
            hit = _cache.get(key)
            if hit:
                return hit
        snap = unpack_snapshot(row.data)
        snap.overrides = overrides
        enrich_players(snap.players, snap.settings.season)
        analysis = LeagueAnalysis(league.id, row.id, snap)
        with _cache_lock:
            for k in [k for k in _cache if k[0] == league.id]:
                del _cache[k]
            _cache[key] = analysis
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
        return analysis


def invalidate(league_id: str) -> None:
    with _cache_lock:
        for k in [k for k in _cache if k[0] == league_id]:
            del _cache[k]


def snapshot_count(db: Session, league_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Snapshot).where(Snapshot.league_id == league_id)) or 0
