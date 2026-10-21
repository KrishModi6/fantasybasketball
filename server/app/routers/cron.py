"""External trigger for scheduled refreshes.

Scale-to-zero hosts (Cloud Run, Render free) stop the in-process scheduler when idle, so a
cron service (Cloud Scheduler, GitHub Actions, cron-job.org) calls this endpoint instead.
Disabled unless FBB_CRON_TOKEN is set; the caller sends it in the X-Cron-Token header.
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Header, HTTPException

from ..config import get_settings
from ..db import SessionLocal
from ..orm import League
from ..services.sync import due_leagues, run_sync

router = APIRouter(prefix="/api/cron", tags=["cron"])
TIME_BUDGET_S = 240  # stay well inside typical 300 s request timeouts; the rest waits for the next run


@router.post("/sync")
def cron_sync(x_cron_token: str | None = Header(default=None)):
    token = get_settings().cron_token
    if not token or not x_cron_token or not hmac.compare_digest(x_cron_token, token):
        raise HTTPException(404, "Not found")
    started = time.monotonic()
    results = []
    for league_id in due_leagues():
        if time.monotonic() - started > TIME_BUDGET_S:
            results.append({"league_id": league_id, "status": "deferred"})
            continue
        run_sync(league_id, reason="scheduled")
        with SessionLocal() as db:
            lg = db.get(League, league_id)
            results.append({"league_id": league_id, "status": lg.last_sync_status if lg else "deleted"})
    return {"synced": sum(r["status"] == "ok" for r in results), "results": results,
            "seconds": round(time.monotonic() - started, 1)}
