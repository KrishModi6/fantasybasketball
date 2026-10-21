"""Shared fixtures."""

from __future__ import annotations

import pytest

from factories import make_snapshot
from fbb.models import LeagueSnapshot


@pytest.fixture
def snapshot() -> LeagueSnapshot:
    return make_snapshot()
