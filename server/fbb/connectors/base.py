"""Connector interface.

A connector turns some external source into a ``LeagueSnapshot``. The server
stores connector config per league (credentials encrypted at rest) and calls
``fetch_snapshot`` on a schedule and on demand.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..models import LeagueSnapshot


class ConnectorError(RuntimeError):
    """Raised with a user-facing message when a source can't be read."""


@dataclass
class ConnectorInfo:
    key: str
    name: str
    status: str  # "available" | "needs_credentials" | "unverified"
    description: str
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)


class LeagueConnector(abc.ABC):
    key: str = ""

    @abc.abstractmethod
    def fetch_snapshot(self) -> LeagueSnapshot:
        """Return the full league state. Must raise ConnectorError on failure."""

    @classmethod
    @abc.abstractmethod
    def info(cls) -> ConnectorInfo: ...
