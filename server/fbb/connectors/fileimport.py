"""File-based league import (the fallback that works for any platform).

Accepted uploads
  * CSV with columns ``team, player`` and optionally ``owner, slot, player_id``
  * JSON "roster spec":
      {"name": "...", "season": 2027,
       "scoring": {"kind": "points", "points": {"PTS": 1, ...}},      (optional)
       "roster_slots": {"PG": 1, ...},                                  (optional)
       "teams": [{"name": "...", "owner": "...", "players": ["Nikola Jokic", ...]}]}
  * JSON full snapshot previously exported by this app (``LeagueSnapshot``)

Rosters come from the file; player stats, projections, injuries and the NBA
schedule are refreshed from ESPN's public player pool on every sync, so a file
league is still "live" for everything except roster moves.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import datetime, timezone

from ..constants import BASE_POSITIONS, DEFAULT_POINTS_SCORING, DEFAULT_ROSTER_SLOTS, SLOT_CODE_TO_ESPN
from ..models import FantasyTeam, LeagueSettings, LeagueSnapshot, Matchup, RosterEntry, ScoringSettings
from ..schedule import build_matchup_periods
from .base import ConnectorError, ConnectorInfo, LeagueConnector

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = _SUFFIX.sub("", s)
    s = re.sub(r"[^a-z ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_upload(filename: str, content: bytes) -> dict:
    """Return a roster spec dict (or {"snapshot": {...}} for a full export)."""
    text = content.decode("utf-8-sig", errors="replace")
    name = filename.lower()
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConnectorError(f"Invalid JSON: {exc}") from exc
        if isinstance(data, dict) and "players" in data and "schedule" in data and "settings" in data:
            LeagueSnapshot.model_validate(data)  # raises if malformed
            return {"snapshot": data}
        if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
            raise ConnectorError('JSON must contain a "teams" list (see docs/CONNECTORS.md).')
        teams = []
        for i, t in enumerate(data["teams"], 1):
            players = []
            for p in t.get("players", []):
                players.append(p if isinstance(p, dict) else {"name": str(p)})
            teams.append({"name": t.get("name") or f"Team {i}", "owner": t.get("owner"), "players": players})
        return {"name": data.get("name"), "season": data.get("season"), "scoring": data.get("scoring"),
                "roster_slots": data.get("roster_slots"), "teams": teams}
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
        if "team" not in cols or "player" not in cols:
            raise ConnectorError("CSV needs at least 'team' and 'player' columns.")
        teams: dict[str, dict] = {}
        for row in reader:
            tname = (row[cols["team"]] or "").strip()
            pname = (row[cols["player"]] or "").strip()
            if not tname or not pname:
                continue
            t = teams.setdefault(tname, {"name": tname, "owner": None, "players": []})
            if "owner" in cols and row.get(cols["owner"]):
                t["owner"] = row[cols["owner"]].strip()
            entry = {"name": pname}
            if "slot" in cols and row.get(cols["slot"]):
                entry["slot"] = row[cols["slot"]].strip().upper()
            if "player_id" in cols and row.get(cols["player_id"]):
                entry["player_id"] = row[cols["player_id"]].strip()
            t["players"].append(entry)
        if not teams:
            raise ConnectorError("No team/player rows found in the CSV.")
        return {"teams": list(teams.values())}
    raise ConnectorError("Upload a .csv or .json file.")


class FileConnector(LeagueConnector):
    key = "file"

    def __init__(self, spec: dict, season: int, name: str):
        self.spec = spec
        self.season = season
        self.name = name

    @classmethod
    def info(cls) -> ConnectorInfo:
        return ConnectorInfo(key=cls.key, name="File import (CSV / JSON)", status="available",
                             description="Rosters from a file; stats and projections refreshed from ESPN's public data.")

    def fetch_snapshot(self) -> LeagueSnapshot:
        if "snapshot" in self.spec:
            snap = LeagueSnapshot.model_validate(self.spec["snapshot"])
            snap.source = "file"
            return snap
        from .demo import DemoConnector

        # Reuse the demo connector's cached ESPN pool/schedule fetch, then replace its rosters.
        base = DemoConnector(season=self.season, team_count=4, name=self.name).fetch_snapshot()
        for p in base.players.values():
            p.fantasy_team_id = None
        by_name: dict[str, str] = {}
        for pid, p in base.players.items():
            by_name.setdefault(normalize_name(p.name), pid)

        warnings: list[str] = []
        teams: list[FantasyTeam] = []
        for i, t in enumerate(self.spec.get("teams", []), 1):
            ft = FantasyTeam(id=i, name=t["name"][:60], abbrev=_abbrev(t["name"]), owner_name=t.get("owner"))
            for entry in t.get("players", []):
                pid = str(entry.get("player_id") or "") or by_name.get(normalize_name(entry["name"]))
                if not pid or pid not in base.players:
                    warnings.append(f"{t['name']}: couldn't match '{entry.get('name')}'")
                    continue
                slot = entry.get("slot", "BE")
                ft.roster.append(RosterEntry(player_id=pid, slot=slot if slot in SLOT_CODE_TO_ESPN else "BE"))
                base.players[pid].fantasy_team_id = i
            teams.append(ft)
        if len(teams) < 2:
            raise ConnectorError("A league needs at least two teams.")

        scoring = ScoringSettings.model_validate(self.spec["scoring"]) if self.spec.get("scoring") else \
            ScoringSettings(kind="points", points=dict(DEFAULT_POINTS_SCORING))
        settings = LeagueSettings(
            name=self.name, season=self.season, scoring=scoring,
            roster_slots=self.spec.get("roster_slots") or dict(DEFAULT_ROSTER_SLOTS),
            team_count=len(teams), regular_season_matchups=int(self.spec.get("regular_season_matchups", 19)),
            playoff_teams=int(self.spec.get("playoff_teams", 4)),
            playoff_round_weeks=list(self.spec.get("playoff_round_weeks", [2, 2])),
        )
        settings.matchup_periods = build_matchup_periods(base.schedule, settings.regular_season_matchups,
                                                         settings.playoff_round_weeks)
        from ..strength import round_robin

        rr = round_robin([t.id for t in teams])
        matchups = [Matchup(matchup_period=mp, home_team_id=a, away_team_id=b)
                    for mp in range(1, settings.regular_season_matchups + 1)
                    for a, b in rr[(mp - 1) % len(rr)]]
        records = self.spec.get("records") or {}
        for t in teams:
            rec = records.get(t.name) or {}
            t.wins, t.losses, t.ties = int(rec.get("wins", 0)), int(rec.get("losses", 0)), int(rec.get("ties", 0))
        current_mp = next((mp for mp, days in settings.matchup_periods.items() if base.current_period in days), 1)
        return LeagueSnapshot(
            settings=settings, teams=teams, players=base.players, matchups=matchups, schedule=base.schedule,
            current_period=base.current_period, current_matchup_period=current_mp, news=base.news,
            source="file", fetched_at=datetime.now(timezone.utc), warnings=warnings,
        )


def _abbrev(name: str) -> str:
    letters = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name))[:4].upper()
    return letters or name[:3].upper()


def example_csv() -> str:
    return "team,owner,player\nCourt Vision,Alex,Nikola Jokic\nCourt Vision,Alex,Anthony Edwards\n" \
           "Glass Cleaners,Jordan,Victor Wembanyama\nGlass Cleaners,Jordan,Luka Doncic\n"


__all__ = ["FileConnector", "parse_upload", "normalize_name", "example_csv", "BASE_POSITIONS"]
