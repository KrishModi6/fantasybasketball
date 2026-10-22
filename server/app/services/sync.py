"""Pull fresh data for a league and store it as a new snapshot."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from fbb.connectors.base import ConnectorError, LeagueConnector
from fbb.connectors.demo import DemoConnector
from fbb.connectors.espn import EspnConnector
from fbb.connectors.fileimport import FileConnector
from fbb.connectors.flaim import FlaimConnector
from fbb.enrich import enrich_players, fetch_missing_bios
from fbb.models import LeagueSnapshot
from fbb.projections import ProjectionEngine

from ..config import get_settings
from ..db import SessionLocal
from ..orm import Activity, League, LeagueMember, ProjectionHistory, Snapshot
from ..realtime import hub
from ..security import decrypt_json
from .analysis import invalidate, latest_snapshot_row, models, pack_snapshot, unpack_snapshot
from .board import MOVE_SOURCES, describe_move, refresh_board, roster_moves

log = logging.getLogger(__name__)
KEEP_SNAPSHOTS = 20


def build_connector(league: League) -> LeagueConnector:
    cfg = decrypt_json(league.connector_config)
    if league.source == "espn":
        return EspnConnector(cfg["league_id"], league.season, cfg.get("espn_s2"), cfg.get("swid"))
    if league.source == "demo":
        return DemoConnector(season=league.season, team_count=int(cfg.get("team_count", 10)),
                             seed=int(cfg.get("seed", 7)), name=league.name)
    if league.source == "file":
        return FileConnector(cfg.get("spec", {}), league.season, league.name)
    if league.source == "flaim":
        return FlaimConnector(cfg["flaim_token"], cfg.get("platform", "espn"), cfg["league_id"], league.season,
                              league.name, url=get_settings().flaim_mcp_url)
    raise ConnectorError(f"Unknown source {league.source}")


def _log(db, league_id: str, kind: str, message: str, payload: dict | None = None) -> None:
    db.add(Activity(league_id=league_id, kind=kind, message=message, payload=payload or {}))


def _injury_changes(prev: LeagueSnapshot | None, new: LeagueSnapshot) -> list[tuple[str, str, str, str]]:
    if prev is None:
        return []
    rostered = {e.player_id for t in new.teams for e in t.roster}
    out = []
    for pid in rostered:
        a = prev.players.get(pid)
        b = new.players.get(pid)
        if a and b and (a.injury_status or "ACTIVE") != (b.injury_status or "ACTIVE"):
            out.append((pid, b.name, a.injury_status or "ACTIVE", b.injury_status or "ACTIVE"))
    return out


def run_sync(league_id: str, reason: str = "manual") -> None:
    """Fetch, store and post-process one league. Safe to call from a worker thread."""
    db = SessionLocal()
    try:
        league = db.get(League, league_id)
        if league is None:
            return
        league.last_sync_status = "running"
        db.commit()
        hub.publish(league_id, {"type": "sync_started", "reason": reason})
        try:
            snap = build_connector(league).fetch_snapshot()
        except ConnectorError as exc:
            _fail(db, league, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("sync failed for %s", league_id)
            _fail(db, league, f"Unexpected error while syncing: {exc}")
            return

        prev_row = latest_snapshot_row(db, league_id)
        prev = unpack_snapshot(prev_row.data) if prev_row else None
        # Keep league-specific overrides of settings that the source can't express (file leagues).
        if league.name and snap.source in ("demo", "file"):
            snap.settings.name = league.name
        try:
            fetch_missing_bios([pid for pid in snap.players], max_fetch=60)
        except Exception:  # noqa: BLE001 - bios are optional
            pass
        db.add(Snapshot(league_id=league_id, source=snap.source, data=pack_snapshot(snap)))
        _prune(db, league_id)

        for pid, name, old, new in _injury_changes(prev, snap):
            _log(db, league_id, "injury", f"{name}: {old.replace('_', ' ').title()} → {new.replace('_', ' ').title()}",
                 {"player_id": pid, "from": old, "to": new})
        for w in snap.warnings[:20]:
            _log(db, league_id, "warning", w)
        moves = roster_moves(prev, snap) if league.source in MOVE_SOURCES else []
        for move in moves:
            _log(db, league_id, "transaction", describe_move(move, prev, snap), move)

        _record_projection_history(db, league_id, snap)
        claim_own_team(db, league, snap)
        league.season = snap.settings.season
        db.commit()
        invalidate(league_id)
        # Re-score the board before marking the refresh done, so anyone who sees the new sync time (by
        # push or by polling) also gets the new board. This also warms the analysis cache for page loads.
        try:
            refresh_board(db, league)
        except Exception:  # noqa: BLE001 - a stale board must never fail the refresh itself
            log.exception("board refresh failed for %s", league_id)
            db.rollback()
        league.last_synced_at = datetime.now(timezone.utc)
        league.last_sync_status = "ok"
        league.last_sync_error = None
        db.commit()
        if moves:
            hub.publish(league_id, {"type": "roster_moves", "count": len(moves),
                                    "first": describe_move(moves[0], prev, snap)})
        hub.publish(league_id, {"type": "sync_completed", "reason": reason, "warnings": len(snap.warnings)})
    finally:
        db.close()


def _swid(value: str | None) -> str:
    return (value or "").strip().strip("{}").upper()


def claim_own_team(db, league: League, snap: LeagueSnapshot) -> None:
    """Give whoever connected an ESPN league the team ESPN says their login owns, if they have none yet.

    ESPN lists each team's owners by member id, which is the same GUID as the SWID cookie. Copies
    joined with an invite code are skipped: their cookies belong to whoever sent the code.
    """
    if league.source != "espn":
        return
    cfg = decrypt_json(league.connector_config)
    swid = _swid(cfg.get("swid"))
    if not swid or cfg.get("shared_by"):
        return
    creator = db.scalar(select(LeagueMember).where(LeagueMember.league_id == league.id,
                                                   LeagueMember.user_id == league.owner_id))
    if creator is None or creator.team_id is not None:
        return
    team = next((t for t in snap.teams if swid in {_swid(o) for o in t.owner_ids}), None)
    taken = team is not None and db.scalar(select(LeagueMember).where(LeagueMember.league_id == league.id,
                                                                     LeagueMember.team_id == team.id))
    if team is None or taken:
        return
    creator.team_id = team.id
    _log(db, league.id, "member", f"{team.name} was matched to the ESPN login that owns it", {"team_id": team.id})


def _fail(db, league: League, message: str) -> None:
    league.last_sync_status = "error"
    league.last_sync_error = message[:1000]
    _log(db, league.id, "sync_error", message[:480])
    db.commit()
    hub.publish(league.id, {"type": "sync_failed", "error": message})


def _prune(db, league_id: str) -> None:
    ids = db.scalars(select(Snapshot.id).where(Snapshot.league_id == league_id)
                     .order_by(Snapshot.created_at.desc(), Snapshot.id.desc()).offset(KEEP_SNAPSHOTS)).all()
    if ids:
        db.execute(delete(Snapshot).where(Snapshot.id.in_(ids)))


def _record_projection_history(db, league_id: str, snap: LeagueSnapshot) -> None:
    """Upsert today's projection for every rostered + top free-agent player (time series for trend charts)."""
    enrich_players(snap.players, snap.settings.season)
    proj = ProjectionEngine(snap, models.get()).project_all()
    today = snap.schedule.period_dates.get(snap.current_period) or date.today()
    rostered = {e.player_id for t in snap.teams for e in t.roster}
    keep = rostered | {pid for pid, _ in sorted(((pid, -p.fp_pg) for pid, p in proj.items()),
                                                key=lambda x: x[1])[:250]}
    db.execute(delete(ProjectionHistory).where(ProjectionHistory.league_id == league_id,
                                               ProjectionHistory.as_of == today))
    for pid in keep:
        pr = proj.get(pid)
        if pr is None:
            continue
        db.add(ProjectionHistory(league_id=league_id, player_id=pid, as_of=today, fp_pg=round(pr.fp_pg, 3),
                                 mpg=round(pr.mpg, 2), availability=round(pr.availability, 3),
                                 games_out=round(pr.games_out, 1),
                                 injury_status=snap.players[pid].injury_status or "ACTIVE"))


def due_leagues(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        out = []
        for lg in db.scalars(select(League)).all():
            if lg.last_sync_status == "running":
                continue
            interval = timedelta(hours=lg.sync_interval_hours or get_settings().sync_interval_hours)
            last = lg.last_synced_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last is None or now - last >= interval:
                out.append(lg.id)
        return out
    finally:
        db.close()
