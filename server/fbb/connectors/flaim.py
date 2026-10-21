"""Flaim connector (UNVERIFIED).

Flaim (flaim.app, github.com/jdguggs10/flaim) exposes ESPN / Yahoo / Sleeper
leagues to AI assistants through a single MCP endpoint
(``https://api.flaim.app/mcp``) with read-only tools such as
``get_league_info``, ``get_roster``, ``get_standings`` and ``get_matchups``.

It has no documented REST API, and its MCP endpoint is protected by an OAuth
sign-in meant for AI clients. This adapter speaks MCP's Streamable-HTTP
JSON-RPC transport with a bearer token you supply; the tool *output* format
isn't published, so results are mapped defensively into the same roster spec
the file importer uses and players are matched to ESPN's pool by name.

To enable it you need: a Flaim access token (``flaim_token``) obtained through
Flaim's OAuth flow, plus the platform and league id Flaim knows your league by.
Until then use the ESPN connector (direct and verified) or a CSV/JSON import.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import ConnectorError, ConnectorInfo, LeagueConnector
from .fileimport import FileConnector

PROTOCOL_VERSION = "2025-06-18"


class McpHttpClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url = url
        self.http = httpx.Client(timeout=timeout, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })
        self.session_id: str | None = None
        self._id = 0

    def close(self) -> None:
        self.http.close()

    def rpc(self, method: str, params: dict | None = None, notify: bool = False) -> Any:
        self._id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = self._id
        headers = {"Mcp-Session-Id": self.session_id} if self.session_id else {}
        r = self.http.post(self.url, json=body, headers=headers)
        if r.status_code in (401, 403):
            raise ConnectorError("Flaim rejected the access token (sign in again through Flaim).")
        r.raise_for_status()
        if "mcp-session-id" in r.headers:
            self.session_id = r.headers["mcp-session-id"]
        if notify:
            return None
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    msg = json.loads(line[5:].strip())
                    if msg.get("id") == self._id:
                        return _unwrap(msg)
            raise ConnectorError("Flaim returned an empty event stream.")
        return _unwrap(r.json())

    def initialize(self) -> dict:
        res = self.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": {"name": "fbb-analytics", "version": "0.1"}})
        self.rpc("notifications/initialized", notify=True)
        return res

    def call(self, tool: str, args: dict) -> Any:
        res = self.rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            raise ConnectorError(f"Flaim tool {tool} failed: {res.get('content')}")
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return res.get("structuredContent") or joined


def _unwrap(msg: dict) -> Any:
    if "error" in msg:
        raise ConnectorError(f"Flaim MCP error: {msg['error'].get('message')}")
    return msg.get("result", {})


def _find_teams(obj: Any) -> list[dict]:
    """Find a list of team-like dicts (name + roster/players) anywhere in a payload."""
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        keys = set().union(*(x.keys() for x in obj))
        if keys & {"roster", "players", "entries"} and keys & {"name", "teamName", "team_name", "displayName"}:
            return obj
        for x in obj:
            found = _find_teams(x)
            if found:
                return found
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_teams(v)
            if found:
                return found
    return []


def _player_name(p: Any) -> str | None:
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        for k in ("fullName", "name", "playerName", "full_name", "displayName"):
            if isinstance(p.get(k), str):
                return p[k]
        for k in ("player", "playerPoolEntry"):
            if isinstance(p.get(k), dict):
                return _player_name(p[k])
    return None


class FlaimConnector(LeagueConnector):
    key = "flaim"

    def __init__(self, token: str, platform: str, league_id: str, season: int, name: str,
                 url: str = "https://api.flaim.app/mcp"):
        self.token, self.platform, self.league_id = token, platform, league_id
        self.season, self.name, self.url = season, name, url

    @classmethod
    def info(cls) -> ConnectorInfo:
        return ConnectorInfo(
            key=cls.key, name="Flaim (MCP)", status="unverified",
            description="Read-only league data via Flaim's MCP server. Requires a Flaim OAuth access token; "
                        "tool output format is unpublished, so this adapter is best-effort.",
            required_fields=["flaim_token", "platform", "league_id", "season"],
        )

    def fetch_snapshot(self):
        client = McpHttpClient(self.url, self.token)
        try:
            client.initialize()
            tools = {t["name"] for t in client.rpc("tools/list").get("tools", [])}
            needed = {"get_league_info", "get_roster"}
            if not needed <= tools:
                raise ConnectorError(f"Flaim is missing expected tools: {sorted(needed - tools)}")
            args = {"platform": self.platform, "league_id": self.league_id, "sport": "basketball",
                    "season": self.season}
            info = client.call("get_league_info", args)
            teams = _find_teams(info) or _find_teams(client.call("get_roster", args))
            standings = client.call("get_standings", args) if "get_standings" in tools else None
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Couldn't reach Flaim: {exc}") from exc
        finally:
            client.close()
        if not teams:
            raise ConnectorError("Flaim responded, but no rosters were recognisable in its output.")
        spec_teams = []
        for i, t in enumerate(teams, 1):
            roster = t.get("roster") or t.get("players") or t.get("entries") or []
            if isinstance(roster, dict):
                roster = roster.get("entries") or roster.get("players") or []
            players = [{"name": n} for n in (_player_name(p) for p in roster) if n]
            spec_teams.append({"name": t.get("name") or t.get("teamName") or t.get("team_name") or f"Team {i}",
                               "owner": t.get("owner") or t.get("ownerName"), "players": players})
        spec = {"teams": spec_teams}
        if isinstance(standings, (dict, list)):
            recs = {}
            for t in _find_records(standings):
                recs[t["name"]] = t
            spec["records"] = recs
        snap = FileConnector(spec, self.season, self.name).fetch_snapshot()
        snap.source = "flaim"
        return snap


def _find_records(obj: Any) -> list[dict]:
    out = []
    if isinstance(obj, list):
        for x in obj:
            out += _find_records(x)
    elif isinstance(obj, dict):
        name = obj.get("name") or obj.get("teamName")
        if name and any(k in obj for k in ("wins", "losses")):
            out.append({"name": name, "wins": obj.get("wins", 0), "losses": obj.get("losses", 0),
                        "ties": obj.get("ties", 0)})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                out += _find_records(v)
    return out
