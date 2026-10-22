"""Courtside engine for the desktop app: the API server on 127.0.0.1 for one local user.

The Electron shell starts the PyInstaller build of this file, waits for /api/health and points
its window at it. Settings arrive as FBB_* environment variables (see desktop/main.js).

  courtside-engine --port 52714
  courtside-engine --self-test     # used by the release workflow on the built program
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path


def _local_defaults() -> None:
    os.environ.setdefault("FBB_LOCAL_MODE", "true")
    os.environ.setdefault("FBB_RETRAIN_ENABLED", "false")  # the bundled model ships with each release


def _server(port: int):
    import uvicorn

    from app.main import app

    # log_config=None keeps the app's own logging setup; the shell captures stdout into engine.log.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False, ws="websockets")
    return uvicorn.Server(config)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def self_test() -> int:
    """Start the real server against a throwaway database and exercise the bundled pieces."""
    import httpx

    tmp = Path(tempfile.mkdtemp(prefix="courtside-selftest-"))
    os.environ.update(FBB_DATABASE_URL=f"sqlite:///{(tmp / 'test.db').as_posix()}", FBB_CACHE_DIR=str(tmp / "cache"),
                      FBB_SECRET_KEY="self-test-" + "x" * 40, FBB_SCHEDULER_ENABLED="false")
    _local_defaults()
    port = _free_port()
    server = _server(port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("the server did not start")
        with httpx.Client(base_url=base, timeout=20) as c:
            assert c.get("/api/health").status_code == 200, "health"
            c.headers["Authorization"] = f"Bearer {c.post('/api/auth/local').json()['token']}"
            model = c.get("/api/model").json()
            assert model.get("trained"), f"bundled model missing: {model}"
            assert c.get("/api/leagues").status_code == 200, "leagues"
            assert c.get("/api/ai/status").status_code == 200, "ai status"
        from app.services.analysis import models

        assert models.get() is not None, "projection model failed to load"
        print("Courtside engine self-test passed")
        return 0
    except Exception as exc:  # noqa: BLE001 - report any failure as a non-zero exit
        print(f"Courtside engine self-test FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(prog="courtside-engine")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    _local_defaults()
    _server(args.port).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
