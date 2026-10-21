"""WebSocket fan-out of league events (sync finished, trade shared, member joined, ...).

Single-process hub. To run several API instances, replace ``broadcast`` with a
Redis pub/sub publish and have each instance relay to its own sockets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def join(self, league_id: str, ws: WebSocket) -> None:
        self.rooms[league_id].add(ws)

    def leave(self, league_id: str, ws: WebSocket) -> None:
        self.rooms[league_id].discard(ws)

    async def broadcast(self, league_id: str, event: dict) -> None:
        event = {**event, "at": datetime.now(timezone.utc).isoformat()}
        payload = json.dumps(event, default=str)
        dead = []
        for ws in list(self.rooms.get(league_id, ())):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - client went away
                dead.append(ws)
        for ws in dead:
            self.leave(league_id, ws)

    def publish(self, league_id: str, event: dict) -> None:
        """Thread-safe publish (sync jobs run in worker threads)."""
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self.broadcast(league_id, event))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast(league_id, event), self.loop)


hub = Hub()
