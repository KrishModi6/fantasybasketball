"""Background refresh loop: re-syncs connected leagues and (optionally) retrains the model."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time

from fastapi.concurrency import run_in_threadpool

from .config import SERVER_DIR, get_settings
from .services.sync import due_leagues, run_sync

log = logging.getLogger(__name__)
TICK_SECONDS = 600
MAX_PARALLEL = 2


async def refresh_loop() -> None:
    sem = asyncio.Semaphore(MAX_PARALLEL)  # stay gentle with ESPN's rate limits
    last_retrain = time.time()
    while True:
        try:
            ids = await run_in_threadpool(due_leagues)

            async def one(lid: str):
                async with sem:
                    await run_in_threadpool(run_sync, lid, "scheduled")

            await asyncio.gather(*(one(i) for i in ids))
            s = get_settings()
            if s.retrain_enabled and time.time() - last_retrain > s.retrain_interval_days * 86400:
                last_retrain = time.time()
                await run_in_threadpool(retrain)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("refresh loop iteration failed")
        await asyncio.sleep(TICK_SECONDS)


def retrain() -> None:
    """Refresh the two most recent seasons of history, then retrain. The model hot-reloads on file change."""
    py = sys.executable
    for cmd in ([py, "-m", "scripts.build_history", "--first", "2018", "--skip-ages"],
                [py, "-m", "scripts.train_model"]):
        res = subprocess.run(cmd, cwd=SERVER_DIR, capture_output=True, text=True, timeout=3600)
        if res.returncode != 0:
            log.error("retrain step failed: %s\n%s", " ".join(cmd), res.stderr[-2000:])
            return
    log.info("model retrained")
