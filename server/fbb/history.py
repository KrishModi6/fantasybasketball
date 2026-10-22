"""Loader for the historical dataset produced by ``scripts/build_history.py``."""

from __future__ import annotations

import csv
import os
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .constants import COUNTING_STATS
from .models import ExpertProjection, SeasonLine

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HISTORY_DIR = DATA_DIR / "history"
# Writable cache (ESPN responses, bios fetched at runtime). Installed desktop builds point this at the
# user's app-data folder, because the bundled data folder may be read-only.
CACHE_DIR = Path(os.environ.get("FBB_CACHE_DIR") or DATA_DIR / "cache")


UNDRAFTED_ADP = 140.0


def clean_adp(adp: float | None) -> float | None:
    """Map ESPN's undrafted sentinels (0, ~140+) to a single 'undrafted' value."""
    if adp is None:
        return None
    if adp <= 0 or adp >= UNDRAFTED_ADP - 0.5:
        return UNDRAFTED_ADP
    return adp


@dataclass
class HistoryDataset:
    lines: dict[str, list[SeasonLine]] = field(default_factory=dict)  # player -> seasons (ascending)
    espn: dict[tuple[str, int], ExpertProjection] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    positions: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    adp: dict[tuple[str, int], float] = field(default_factory=dict)
    gp: dict[tuple[str, int], float] = field(default_factory=dict)  # includes 0-GP rows
    adp_missing_seasons: list[int] = field(default_factory=list)
    age_now: dict[str, float] = field(default_factory=dict)  # age at the start of ``age_season``
    debut_year: dict[str, int] = field(default_factory=dict)
    age_season: int = 2027

    def age_in(self, pid: str, season: int) -> float | None:
        a = self.age_now.get(pid)
        if a is None:
            return None
        return a - (self.age_season - season)

    def seasons(self) -> list[int]:
        return sorted({ln.season for v in self.lines.values() for ln in v})

    def all_lines(self) -> list[SeasonLine]:
        return [ln for v in self.lines.values() for ln in v]


def _f(x: str) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except ValueError:
        return None


@lru_cache(maxsize=2)
def load_history(directory: str | None = None) -> HistoryDataset:
    d = Path(directory) if directory else HISTORY_DIR
    ds = HistoryDataset()
    path = d / "player_seasons.csv"
    if not path.exists():
        return ds
    raw_adp: dict[int, list[tuple[str, float]]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = row["player_id"]
            season = int(row["season"])
            ds.names[pid] = row["name"]
            ds.positions[(pid, season)] = [p for p in row["positions"].split("|") if p]
            if _f(row.get("adp")) is not None:
                raw_adp.setdefault(season, []).append((pid, float(row["adp"])))
            gp = _f(row["gp"]) or 0.0
            ds.gp[(pid, season)] = gp
            if gp > 0:
                ds.lines.setdefault(pid, []).append(
                    SeasonLine(
                        season=season,
                        team=row["team"],
                        gp=gp,
                        minutes=_f(row["min"]) or 0.0,
                        stats={s: _f(row[s]) or 0.0 for s in COUNTING_STATS},
                    )
                )
            egp = _f(row.get("espn_gp"))
            empg = _f(row.get("espn_mpg"))
            if egp and empg:
                ds.espn[(pid, season)] = ExpertProjection(
                    source="ESPN",
                    season=season,
                    gp=egp,
                    minutes_per_game=empg,
                    per_game={s: _f(row[f"espn_{s}"]) for s in COUNTING_STATS if _f(row[f"espn_{s}"]) is not None},
                )
    for v in ds.lines.values():
        v.sort(key=lambda ln: ln.season)
    # ESPN reports undrafted players at ~140 (or 0), and overwrites a finished season's ADP once the
    # next draft season opens. Keep ADP only for seasons where real draft positions survive.
    for season, vals in raw_adp.items():
        drafted = sum(1 for _, a in vals if 0 < a < UNDRAFTED_ADP - 0.5)
        if drafted < 0.05 * len(vals):
            ds.adp_missing_seasons.append(season)
            continue
        for pid, a in vals:
            ds.adp[(pid, season)] = clean_adp(a)
    bios = d / "athletes.json"
    if bios.exists():
        for pid, info in json.loads(bios.read_text()).items():
            if info.get("age") is not None:
                ds.age_now[pid] = float(info["age"])
            if info.get("debut_year"):
                ds.debut_year[pid] = int(info["debut_year"])
    return ds
