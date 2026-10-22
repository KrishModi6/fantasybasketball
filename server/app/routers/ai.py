from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import Membership, current_user, membership
from ..services.ai import PRESETS, AIError, ai_mode, run_assistant
from ..services.analysis import LeagueAnalysis
from .analysis import analysis

router = APIRouter(tags=["ai"])


class AskIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=2000)
    preset: str | None = Field(default=None, max_length=40)
    previous_response_id: str | None = Field(default=None, max_length=200)


@router.get("/api/ai/status")
def status(_=Depends(current_user)):
    return {"mode": ai_mode(), "enabled": ai_mode() != "off", "model": get_settings().ai_model,
            "presets": list(PRESETS)}


@router.post("/api/leagues/{league_id}/ai/ask")
async def ask(body: AskIn, m: Membership = Depends(membership), a: LeagueAnalysis = Depends(analysis)):
    prompt = PRESETS.get(body.preset or "") if body.preset else (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(422, "Ask a question or pick a preset.")
    try:
        return await run_in_threadpool(run_assistant, a, user_id=m.user.id, my_team_id=m.member.team_id,
                                       prompt=prompt, previous_response_id=body.previous_response_id)
    except AIError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
