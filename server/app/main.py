"""FastAPI application entry point.

Run (dev):  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update

from .config import get_settings
from .db import SessionLocal, init_db
from .orm import League, LeagueMember
from .realtime import hub
from .routers import ai, analysis, auth, cron, leagues, matchups, meta, rulings, trades
from .scheduler import refresh_loop
from .security import decode_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().signing_key()  # fail fast if a hosted deployment forgot FBB_SECRET_KEY
    init_db()
    with SessionLocal() as db:  # a crash mid-sync would otherwise leave leagues stuck in "running"
        db.execute(update(League).where(League.last_sync_status == "running").values(last_sync_status="error",
                                                                                     last_sync_error="Interrupted"))
        db.commit()
    hub.loop = asyncio.get_running_loop()
    task = asyncio.create_task(refresh_loop()) if get_settings().scheduler_enabled else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Courtside Analytics API", version="0.1.0", lifespan=lifespan,
              description="Fantasy basketball projections, team strength, trade analysis and league hub.")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class LocalHostOnly:
    """Desktop engine only: answer requests addressed to this computer, nothing else.

    The engine signs its one local user in without a password (routers/auth.py) because it listens on
    127.0.0.1. A web page can still reach that address through a domain it points there (DNS rebinding),
    and the browser then lets it read the answers: the sign-in token, then a league code with the ESPN
    cookies in it. Such a request names the page's domain in its Host header, so it stops here.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and get_settings().local_mode:
            host = dict(scope.get("headers") or []).get(b"host", b"").decode("latin-1")
            if host and host.rsplit(":", 1)[0].lower() not in LOCAL_HOSTS:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": "Courtside's engine only answers this computer."},
                                       status_code=403)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().origins(), allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(LocalHostOnly)  # added last, so it runs first

for r in (auth.router, leagues.router, analysis.router, matchups.router, trades.router, rulings.router, meta.router,
          cron.router, ai.router):
    app.include_router(r)


@app.websocket("/api/ws/leagues/{league_id}")
async def league_socket(ws: WebSocket, league_id: str):
    if not get_settings().realtime_enabled:
        # 4403 tells the client push is off on this host; it falls back to polling.
        await ws.close(code=4403)
        return
    user_id = decode_token(ws.query_params.get("token", ""))
    with SessionLocal() as db:
        ok = user_id and db.scalar(select(LeagueMember).where(LeagueMember.league_id == league_id,
                                                              LeagueMember.user_id == user_id))
    if not ok:
        await ws.close(code=4401)
        return
    await ws.accept()
    await hub.join(league_id, ws)
    try:
        while True:
            await ws.receive_text()  # client pings keep the connection alive
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(league_id, ws)


# Serve the built web app (single-origin deploys). In dev, Vite serves it instead.
_dist = Path(get_settings().web_dist)
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(404, "Not found")
        candidate = _dist / path
        if path and candidate.is_file() and _dist in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
