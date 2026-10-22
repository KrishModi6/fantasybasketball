"""Shared fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest

# Settings are read once and cached, so every test module shares this environment, set before any
# app import.
_tmp = tempfile.mkdtemp(prefix="fbb-test-")
os.environ.setdefault("FBB_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")
os.environ.setdefault("FBB_SCHEDULER_ENABLED", "false")
os.environ.setdefault("FBB_CRON_TOKEN", "test-cron-token")
os.environ.setdefault("FBB_CACHE_DIR", os.path.join(_tmp, "cache"))

# FBB_TEST_POSTGRES=1 runs the API tests against a real (embedded) Postgres instead of SQLite.
# Needs `pip install -r requirements-dev.txt`. Must happen before the app is imported.
if os.environ.get("FBB_TEST_POSTGRES") == "1" and "FBB_DATABASE_URL" not in os.environ:
    import pgserver

    _pg = pgserver.get_server(tempfile.mkdtemp(prefix="fbb-pg-"), cleanup_mode="stop")
    os.environ["FBB_DATABASE_URL"] = _pg.get_uri()
os.environ.setdefault("FBB_DATABASE_URL", f"sqlite:///{_tmp}/test.db")

from factories import make_snapshot
from fbb.models import LeagueSnapshot


@pytest.fixture
def snapshot() -> LeagueSnapshot:
    return make_snapshot()
