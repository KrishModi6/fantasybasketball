"""Shared fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest

# FBB_TEST_POSTGRES=1 runs the API tests against a real (embedded) Postgres instead of SQLite.
# Needs `pip install -r requirements-dev.txt`. Must happen before the app is imported.
if os.environ.get("FBB_TEST_POSTGRES") == "1" and "FBB_DATABASE_URL" not in os.environ:
    import pgserver

    _pg = pgserver.get_server(tempfile.mkdtemp(prefix="fbb-pg-"), cleanup_mode="stop")
    os.environ["FBB_DATABASE_URL"] = _pg.get_uri()

from factories import make_snapshot
from fbb.models import LeagueSnapshot


@pytest.fixture
def snapshot() -> LeagueSnapshot:
    return make_snapshot()
