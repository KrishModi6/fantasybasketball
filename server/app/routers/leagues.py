"""League lifecycle: create/import, settings, sync, members and invites."""

from __future__ import annotations

import base64
import json
import zlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fbb.connectors.base import ConnectorError
from fbb.connectors.fileimport import parse_upload
from fbb.schedule import live_window

from ..config import get_settings
from ..db import get_db
from ..deps import Membership, current_user, membership, owner_membership
from ..orm import Activity, Invite, League, LeagueMember, Snapshot, User
from ..realtime import hub
from ..schemas import (
    ClaimTeam,
    EspnConfig,
    InviteCreate,
    JoinCodeIn,
    LeagueCreate,
    LeagueUpdate,
    MemberUpdate,
    ScoringUpdate,
    ShareCodeIn,
    SharedLeague,
)
from ..security import decrypt_json, encrypt_json, invite_code
from ..services.analysis import invalidate, latest_snapshot_row, pack_snapshot, unpack_snapshot
from ..services.sync import run_sync

router = APIRouter(prefix="/api", tags=["leagues"])
MAX_UPLOAD = 2 * 1024 * 1024


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def league_out(db: Session, lg: League, me: LeagueMember | None = None) -> dict:
    row = latest_snapshot_row(db, lg.id)
    teams, settings = [], None
    if row is not None:
        snap = unpack_snapshot(row.data)
        claimed = {m.team_id: m for m in lg.members if m.team_id is not None}
        teams = [{"id": t.id, "name": t.name, "abbrev": t.abbrev, "owner_name": t.owner_name,
                  "wins": t.wins, "losses": t.losses, "ties": t.ties, "points_for": t.points_for,
                  "points_against": t.points_against,
                  "claimed_by": claimed[t.id].user.display_name if t.id in claimed else None}
                 for t in snap.teams]
        st = snap.settings
        live = live_window(snap.schedule, snap.current_period)
        settings = {
            "scoring": st.scoring.model_dump(), "roster_slots": st.roster_slots, "team_count": st.team_count,
            "regular_season_matchups": st.regular_season_matchups, "playoff_teams": st.playoff_teams,
            "playoff_round_weeks": st.playoff_round_weeks,
            "acquisition_limit_per_matchup": st.acquisition_limit_per_matchup,
            "trade_deadline": st.trade_deadline.isoformat() if st.trade_deadline else None,
            "lineup_lock": st.lineup_lock, "current_matchup_period": snap.current_matchup_period,
            "current_period": snap.current_period,
            "today": (snap.schedule.period_dates.get(snap.current_period).isoformat()
                      if snap.schedule.period_dates.get(snap.current_period) else None),
            "live_window": [t.isoformat() for t in live] if live else None,
            "matchup_periods": {mp: [snap.schedule.period_dates[d[0]].isoformat() if d[0] in snap.schedule.period_dates else None,
                                     snap.schedule.period_dates[d[-1]].isoformat() if d[-1] in snap.schedule.period_dates else None]
                                for mp, d in st.matchup_periods.items() if d},
        }
    cfg = decrypt_json(lg.connector_config)
    return {
        "id": lg.id, "name": lg.name, "season": lg.season, "source": lg.source,
        "sync": {"status": lg.last_sync_status, "error": lg.last_sync_error,
                 "last_synced_at": _aware(lg.last_synced_at).isoformat() if lg.last_synced_at else None,
                 "interval_hours": lg.sync_interval_hours},
        "connector": {"espn_league_id": cfg.get("league_id") if lg.source == "espn" else None,
                      "has_cookies": bool(cfg.get("espn_s2")) if lg.source == "espn" else None,
                      "shared_by": cfg.get("shared_by")},
        "me": {"role": me.role, "team_id": me.team_id} if me else None,
        "member_count": len(lg.members),
        "teams": teams,
        "settings": settings,
    }


def _team_count(db: Session, league_id: str) -> int | None:
    """Teams in the latest snapshot, read straight from the JSON (the full model parse isn't needed here)."""
    row = latest_snapshot_row(db, league_id)
    return len(json.loads(zlib.decompress(row.data))["teams"]) if row is not None else None


@router.get("/leagues")
def my_leagues(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LeagueMember).where(LeagueMember.user_id == user.id)).all()
    out = []
    for m in rows:
        lg = m.league
        out.append({"id": lg.id, "name": lg.name, "season": lg.season, "source": lg.source, "role": m.role,
                    "team_id": m.team_id, "member_count": len(lg.members), "team_count": _team_count(db, lg.id),
                    "sync_status": lg.last_sync_status,
                    "last_synced_at": _aware(lg.last_synced_at).isoformat() if lg.last_synced_at else None})
    return out


def _create(db: Session, user: User, name: str, season: int, source: str, cfg: dict,
            message: str = "created the league") -> League:
    lg = League(name=name, season=season, source=source, owner_id=user.id, connector_config=encrypt_json(cfg),
                sync_interval_hours=get_settings().sync_interval_hours)
    db.add(lg)
    db.flush()
    db.add(LeagueMember(league_id=lg.id, user_id=user.id, role="owner"))
    db.add(Activity(league_id=lg.id, kind="league", message=f"{user.display_name} {message}"))
    db.commit()
    return lg


def _first_sync(db: Session, lg: League, reason: str, keep_on_error: bool = False,
                fallback_error: str = "Could not read that league") -> None:
    """Sync a new league inline so the user lands on a populated league (or sees the error immediately)."""
    run_sync(lg.id, reason=reason)
    db.expire_all()  # the sync wrote through its own session (and may have matched the user's ESPN team)
    if lg.last_sync_status == "error" and not keep_on_error:
        err = lg.last_sync_error
        db.delete(lg)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err or fallback_error)
    if lg.source == "espn" and lg.name.startswith("ESPN league"):
        row = latest_snapshot_row(db, lg.id)
        if row is not None:
            lg.name = unpack_snapshot(row.data).settings.name[:120]
            db.commit()


@router.post("/leagues", status_code=201)
def create_league(body: LeagueCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.source == "espn":
        if not body.espn:
            raise HTTPException(422, "ESPN league id is required")
        cfg = body.espn.model_dump()
        name = body.name or f"ESPN league {body.espn.league_id}"
    elif body.source == "flaim":
        if not body.flaim:
            raise HTTPException(422, "Flaim token and league id are required")
        cfg = body.flaim.model_dump()
        name = body.name or "Flaim league"
    else:
        cfg = {"team_count": body.team_count, "seed": 7}
        name = body.name or "Demo League"
    lg = _create(db, user, name, body.season, body.source, cfg)
    _first_sync(db, lg, "created", keep_on_error=body.source == "demo")
    me = db.scalar(select(LeagueMember).where(LeagueMember.league_id == lg.id, LeagueMember.user_id == user.id))
    return league_out(db, lg, me)


@router.post("/leagues/import", status_code=201)
async def import_league(file: UploadFile = File(...), name: str = Form("Imported league"), season: int = Form(2027),
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (2 MB max)")
    try:
        spec = parse_upload(file.filename or "upload", content)
    except ConnectorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Couldn't read that file: {exc}") from exc
    lg = _create(db, user, (spec.get("name") or name)[:120], int(spec.get("season") or season), "file", {"spec": spec})
    _first_sync(db, lg, "imported", fallback_error="Import failed")
    me = db.scalar(select(LeagueMember).where(LeagueMember.league_id == lg.id, LeagueMember.user_id == user.id))
    return league_out(db, lg, me)


@router.get("/leagues/{league_id}")
def get_league(m: Membership = Depends(membership), db: Session = Depends(get_db)):
    return league_out(db, m.league, m.member)


@router.patch("/leagues/{league_id}")
def update_league(body: LeagueUpdate, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    lg = m.league
    if body.name:
        lg.name = body.name.strip()
    if body.sync_interval_hours:
        lg.sync_interval_hours = body.sync_interval_hours
    if body.espn and lg.source == "espn":
        cfg = decrypt_json(lg.connector_config)
        new = body.espn.model_dump()
        # Blank cookie fields mean "keep what's stored".
        for k in ("espn_s2", "swid"):
            if not new.get(k):
                new[k] = cfg.get(k)
        lg.connector_config = encrypt_json(new)
    db.commit()
    return league_out(db, lg, m.member)


@router.delete("/leagues/{league_id}", status_code=204)
def delete_league(m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    db.delete(m.league)
    db.commit()
    invalidate(m.league.id)


@router.put("/leagues/{league_id}/settings")
def update_scoring(body: ScoringUpdate, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    """Edit scoring for demo/file leagues (ESPN leagues always mirror ESPN's settings)."""
    if m.league.source == "espn":
        raise HTTPException(400, "ESPN leagues use ESPN's scoring settings; change them on ESPN and re-sync.")
    row = latest_snapshot_row(db, m.league.id)
    if row is None:
        raise HTTPException(409, "League has no data yet")
    snap = unpack_snapshot(row.data)
    snap.settings.scoring.kind = body.kind
    if body.kind == "points":
        snap.settings.scoring.points = {k: float(v) for k, v in body.points.items() if v}
    else:
        snap.settings.scoring.categories = body.categories
    if body.roster_slots:
        snap.settings.roster_slots = {k: int(v) for k, v in body.roster_slots.items() if int(v) > 0}
    db.add(Snapshot(league_id=m.league.id, source=snap.source, data=pack_snapshot(snap)))
    if m.league.source == "file":
        cfg = decrypt_json(m.league.connector_config)
        cfg.setdefault("spec", {})["scoring"] = snap.settings.scoring.model_dump()
        if body.roster_slots:
            cfg["spec"]["roster_slots"] = snap.settings.roster_slots
        m.league.connector_config = encrypt_json(cfg)
    db.add(Activity(league_id=m.league.id, kind="settings", message=f"{m.user.display_name} updated scoring settings"))
    db.commit()
    invalidate(m.league.id)
    hub.publish(m.league.id, {"type": "settings_changed"})
    return league_out(db, m.league, m.member)


@router.post("/leagues/{league_id}/sync", status_code=202)
def sync_now(background: BackgroundTasks, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    lg = m.league
    if lg.last_sync_status == "running":
        return {"status": "running"}
    last = _aware(lg.last_synced_at)
    min_gap = timedelta(minutes=get_settings().min_manual_sync_minutes)
    if last and datetime.now(timezone.utc) - last < min_gap and not m.is_owner:
        raise HTTPException(429, "Data was refreshed moments ago; try again in a few minutes.")
    lg.last_sync_status = "running"
    db.commit()
    if get_settings().inline_sync:
        # Scale-to-zero hosts throttle CPU once the response is sent, so finish the sync first.
        run_sync(lg.id, "manual")
        db.refresh(lg)
        return {"status": lg.last_sync_status, "error": lg.last_sync_error}
    background.add_task(run_sync, lg.id, "manual")
    return {"status": "started"}


# ---- members & invites ---------------------------------------------------------


@router.get("/leagues/{league_id}/members")
def members(m: Membership = Depends(membership)):
    return [{"user_id": x.user_id, "display_name": x.user.display_name, "role": x.role, "team_id": x.team_id,
             "joined_at": _aware(x.joined_at).isoformat()} for x in m.league.members]


@router.post("/leagues/{league_id}/members/me/team")
def claim_team(body: ClaimTeam, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    if body.team_id is not None:
        taken = db.scalar(select(LeagueMember).where(LeagueMember.league_id == m.league.id,
                                                     LeagueMember.team_id == body.team_id,
                                                     LeagueMember.user_id != m.user.id))
        if taken:
            raise HTTPException(409, "Another member already manages that team")
        row = latest_snapshot_row(db, m.league.id)
        if row is None or body.team_id not in {t.id for t in unpack_snapshot(row.data).teams}:
            raise HTTPException(404, "No such team in this league")
    m.member.team_id = body.team_id
    db.add(Activity(league_id=m.league.id, kind="member",
                    message=f"{m.user.display_name} {'claimed a team' if body.team_id else 'released their team'}",
                    payload={"team_id": body.team_id}))
    db.commit()
    hub.publish(m.league.id, {"type": "member_updated"})
    return {"team_id": m.member.team_id}


@router.patch("/leagues/{league_id}/members/{user_id}")
def update_member(user_id: str, body: MemberUpdate, m: Membership = Depends(owner_membership),
                  db: Session = Depends(get_db)):
    target = db.scalar(select(LeagueMember).where(LeagueMember.league_id == m.league.id,
                                                  LeagueMember.user_id == user_id))
    if target is None:
        raise HTTPException(404, "Member not found")
    if body.team_id is not None:
        clash = db.scalar(select(LeagueMember).where(LeagueMember.league_id == m.league.id,
                                                     LeagueMember.team_id == body.team_id,
                                                     LeagueMember.user_id != user_id))
        if clash:
            clash.team_id = None
        target.team_id = body.team_id
    if body.role:
        target.role = body.role
    db.commit()
    hub.publish(m.league.id, {"type": "member_updated"})
    return {"ok": True}


@router.delete("/leagues/{league_id}/members/{user_id}", status_code=204)
def remove_member(user_id: str, m: Membership = Depends(membership), db: Session = Depends(get_db)):
    if user_id != m.user.id and not m.is_owner:
        raise HTTPException(403, "Only the league owner can remove other members")
    if user_id == m.league.owner_id:
        raise HTTPException(400, "The league creator can't leave; delete the league instead")
    target = db.scalar(select(LeagueMember).where(LeagueMember.league_id == m.league.id,
                                                  LeagueMember.user_id == user_id))
    if target:
        db.delete(target)
        db.commit()
        hub.publish(m.league.id, {"type": "member_updated"})


def invite_out(inv: Invite) -> dict:
    base = get_settings().public_url.rstrip("/")
    return {"code": inv.code, "url": f"{base}/join/{inv.code}", "uses": inv.uses, "max_uses": inv.max_uses,
            "expires_at": _aware(inv.expires_at).isoformat() if inv.expires_at else None,
            "revoked": inv.revoked, "created_at": _aware(inv.created_at).isoformat()}


@router.get("/leagues/{league_id}/invites")
def list_invites(m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    rows = db.scalars(select(Invite).where(Invite.league_id == m.league.id, Invite.revoked.is_(False))
                      .order_by(Invite.created_at.desc())).all()
    return [invite_out(i) for i in rows]


@router.post("/leagues/{league_id}/invites", status_code=201)
def create_invite(body: InviteCreate, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    code = invite_code()
    while db.scalar(select(Invite).where(Invite.code == code)):
        code = invite_code()
    inv = Invite(league_id=m.league.id, code=code, created_by=m.user.id, max_uses=body.max_uses,
                 expires_at=(datetime.now(timezone.utc) + timedelta(days=body.expires_in_days))
                 if body.expires_in_days else None)
    db.add(inv)
    db.commit()
    return invite_out(inv)


@router.delete("/leagues/{league_id}/invites/{code}", status_code=204)
def revoke_invite(code: str, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    inv = db.scalar(select(Invite).where(Invite.league_id == m.league.id, Invite.code == code.upper()))
    if inv:
        inv.revoked = True
        db.commit()


def _valid_invite(db: Session, code: str) -> Invite:
    inv = db.scalar(select(Invite).where(Invite.code == code.strip().upper()))
    now = datetime.now(timezone.utc)
    if (inv is None or inv.revoked or (inv.expires_at and _aware(inv.expires_at) < now)
            or (inv.max_uses and inv.uses >= inv.max_uses)):
        raise HTTPException(404, "This invite is invalid or has expired")
    return inv


@router.get("/invites/{code}")
def invite_preview(code: str, db: Session = Depends(get_db)):
    """Public preview so the join page can show what you're joining before sign-in."""
    inv = _valid_invite(db, code)
    lg = db.get(League, inv.league_id)
    owner = db.get(User, lg.owner_id)
    row = latest_snapshot_row(db, lg.id)
    claimed = {x.team_id for x in lg.members if x.team_id is not None}
    teams = []
    if row is not None:
        teams = [{"id": t.id, "name": t.name, "claimed": t.id in claimed} for t in unpack_snapshot(row.data).teams]
    return {"league_name": lg.name, "season": lg.season, "owner": owner.display_name if owner else None,
            "member_count": len(lg.members), "teams": teams}


@router.post("/invites/{code}/accept")
def accept_invite(code: str, body: ClaimTeam | None = None, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    inv = _valid_invite(db, code)
    existing = db.scalar(select(LeagueMember).where(LeagueMember.league_id == inv.league_id,
                                                    LeagueMember.user_id == user.id))
    if existing is None:
        member = LeagueMember(league_id=inv.league_id, user_id=user.id, role="member")
        if body and body.team_id is not None:
            taken = db.scalar(select(LeagueMember).where(LeagueMember.league_id == inv.league_id,
                                                         LeagueMember.team_id == body.team_id))
            if not taken:
                member.team_id = body.team_id
        db.add(member)
        inv.uses += 1
        db.add(Activity(league_id=inv.league_id, kind="member", message=f"{user.display_name} joined the league"))
        db.commit()
        hub.publish(inv.league_id, {"type": "member_joined", "name": user.display_name})
    return {"league_id": inv.league_id}


# ---- league codes (desktop app) -------------------------------------------------
# Each desktop app runs its own engine, so link invites can't reach anyone else's computer. A league
# code carries the league's connection instead (for ESPN: the league id and, for private leagues,
# the owner's cookies), so the person joining connects the same league without finding cookies.

CODE_PREFIX = "CS1."
MAX_CODE_JSON = 2 * 1024 * 1024


def _pack_code(shared: SharedLeague) -> str:
    raw = zlib.compress(shared.model_dump_json(exclude_none=True).encode(), 9)
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unpack_code(code: str) -> SharedLeague:
    code = "".join(code.split())  # chat apps wrap long codes across lines
    start = code.find(CODE_PREFIX)
    if start < 0:
        raise HTTPException(422, "That isn't a Courtside league code. League codes start with CS1.")
    body = code[start + len(CODE_PREFIX):]
    try:
        inflate = zlib.decompressobj()
        raw = inflate.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), MAX_CODE_JSON)
        if inflate.unconsumed_tail or not inflate.eof:
            raise ValueError("truncated or oversized")
        shared = SharedLeague.model_validate(json.loads(raw))
    except (ValueError, zlib.error, ValidationError) as exc:
        raise HTTPException(422, "That code is incomplete or damaged. Copy the whole code and try again.") from exc
    if shared.source == "espn" and shared.espn is None:
        raise HTTPException(422, "That code is incomplete or damaged. Copy the whole code and try again.")
    return shared


def _team_ids(db: Session, league_id: str) -> set[int]:
    row = latest_snapshot_row(db, league_id)
    return {t.id for t in unpack_snapshot(row.data).teams} if row is not None else set()


@router.post("/leagues/{league_id}/share-code")
def share_code(body: ShareCodeIn, m: Membership = Depends(owner_membership), db: Session = Depends(get_db)):
    lg = m.league
    cfg = decrypt_json(lg.connector_config)
    if lg.source == "flaim":
        raise HTTPException(400, "Flaim leagues can't be shared with a code; each member connects their own Flaim token.")
    if cfg.get("shared_by") and cfg.get("espn_s2"):
        raise HTTPException(403, f"This league uses {cfg['shared_by']}'s ESPN login, so only they can invite people.")
    if body.team_id is not None and body.team_id not in _team_ids(db, lg.id):
        raise HTTPException(404, "No such team in this league")
    # The desktop app's single local user is called "You", which means nothing to the person joining.
    by = None if get_settings().local_mode else m.user.display_name
    shared = SharedLeague(source=lg.source, name=lg.name, season=lg.season, by=by, team_id=body.team_id)
    if lg.source == "espn":
        shared.espn = EspnConfig(league_id=cfg["league_id"], espn_s2=cfg.get("espn_s2"), swid=cfg.get("swid"))
    elif lg.source == "demo":
        shared.team_count, shared.seed = int(cfg.get("team_count", 10)), int(cfg.get("seed", 7))
    else:
        shared.spec = cfg.get("spec", {})
    return {"code": _pack_code(shared), "includes_login": bool(cfg.get("espn_s2"))}


@router.post("/join-code", status_code=201)
def join_with_code(body: JoinCodeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    shared = _unpack_code(body.code)
    by = shared.by or "the league owner"
    if shared.source == "espn":
        cfg = {**shared.espn.model_dump(), "shared_by": by}
    elif shared.source == "demo":
        cfg = {"team_count": shared.team_count, "seed": shared.seed, "shared_by": by}
    else:
        cfg = {"spec": shared.spec or {}, "shared_by": by}

    # Already connected (the code was pasted twice, or the league was added by hand): open that copy, and
    # if it can't currently read ESPN, switch it to the code's login.
    lg, me = None, None
    if shared.source == "espn":
        for mem in db.scalars(select(LeagueMember).where(LeagueMember.user_id == user.id)).all():
            old = decrypt_json(mem.league.connector_config)
            if (mem.league.source == "espn" and mem.league.season == shared.season
                    and old.get("league_id") == shared.espn.league_id):
                lg, me = mem.league, mem
                break
    if lg is not None:
        if shared.espn.espn_s2 and me.role == "owner" and lg.last_sync_status == "error":
            lg.connector_config = encrypt_json(cfg)
            db.commit()
            _first_sync(db, lg, "joined", keep_on_error=True)
    else:
        lg = _create(db, user, shared.name, shared.season, shared.source, cfg, message="joined with an invite code")
        _first_sync(db, lg, "joined", keep_on_error=shared.source == "demo")
        me = db.scalar(select(LeagueMember).where(LeagueMember.league_id == lg.id, LeagueMember.user_id == user.id))
    if shared.team_id is not None and me.team_id is None and shared.team_id in _team_ids(db, lg.id):
        me.team_id = shared.team_id
        db.commit()
    return league_out(db, lg, me)
