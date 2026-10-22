from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Membership, current_user, membership
from ..orm import User, UserSettings
from ..security import decrypt_json, encrypt_json
from ..services.ai import PRESETS, AIError, ai_mode, check_openai_key, run_assistant
from ..services.analysis import LeagueAnalysis
from .analysis import analysis

router = APIRouter(tags=["ai"])


class AskIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=2000)
    preset: str | None = Field(default=None, max_length=40)
    previous_response_id: str | None = Field(default=None, max_length=200)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=400)


def user_openai_key(db: Session, user_id: str) -> str | None:
    row = db.get(UserSettings, user_id)
    return decrypt_json(row.data).get("openai_api_key") if row else None


@router.get("/api/ai/status")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    key = user_openai_key(db, user.id)
    mode = ai_mode(key)
    return {"mode": mode, "enabled": mode != "off", "model": get_settings().ai_model, "presets": list(PRESETS),
            "own_key": bool(key), "own_key_hint": key[-4:] if key else None,
            "shared": ai_mode() != "off"}  # whether there's a server key or proxy to fall back on


@router.put("/api/ai/key")
async def save_key(body: KeyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Save the user's own OpenAI key; their questions then go straight to OpenAI with it."""
    key = body.api_key.strip()
    if not key.startswith("sk-") or any(c.isspace() for c in key):
        raise HTTPException(422, "That doesn't look like an OpenAI API key. Keys start with sk-.")
    try:
        await run_in_threadpool(check_openai_key, key)
    except AIError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    row = db.get(UserSettings, user.id) or UserSettings(user_id=user.id)
    data = decrypt_json(row.data)
    data["openai_api_key"] = key
    row.data = encrypt_json(data)
    db.add(row)
    db.commit()
    return status(user, db)


@router.delete("/api/ai/key")
def remove_key(user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(UserSettings, user.id)
    if row is not None:
        data = decrypt_json(row.data)
        data.pop("openai_api_key", None)
        row.data = encrypt_json(data)
        db.commit()
    return status(user, db)


@router.post("/api/leagues/{league_id}/ai/ask")
async def ask(body: AskIn, m: Membership = Depends(membership), a: LeagueAnalysis = Depends(analysis),
              db: Session = Depends(get_db)):
    prompt = PRESETS.get(body.preset or "") if body.preset else (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(422, "Ask a question or pick a preset.")
    try:
        return await run_in_threadpool(run_assistant, a, user_id=m.user.id, my_team_id=m.member.team_id,
                                       prompt=prompt, previous_response_id=body.previous_response_id,
                                       api_key=user_openai_key(db, m.user.id))
    except AIError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
