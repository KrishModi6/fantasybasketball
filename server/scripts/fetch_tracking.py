"""Download NBA.com tracking data for xFP: every shot, plus box-score, passing, rebounding and hustle
dashboards for whole seasons and, for the backtest seasons, as of dates early in the season.

Writes (cache, not versioned):
  data/cache/nba/shots_<season>.json.gz      one compact row per field-goal attempt
  data/cache/nba/dash_<season>_<tag>.json    per-player dashboards; tag is "full" or "d<days>" (days after
                                             opening night, the in-season cutoffs of scripts.backtest_xfp)
and (versioned) data/history/nba_ids.json: NBA.com player id -> ESPN id.

stats.nba.com does not answer cloud hosts, so run this from a home connection.

Usage: python -m scripts.fetch_tracking --first 2021 --last 2027 --cutoffs 2024 2025 2026
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.connectors.nba_stats import NbaStatsClient, compact_shot, match_ids  # noqa: E402
from fbb.history import CACHE_DIR, HISTORY_DIR, load_history  # noqa: E402

NBA_DIR = CACHE_DIR / "nba"
CUTOFF_DAYS = (7, 14, 28, 42, 63, 91)  # days after opening night
ESPN_TO_NBA_TEAM = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}

def shots_path(season: int) -> Path:
    return NBA_DIR / f"shots_{season}.json.gz"


def load_shots(season: int) -> list[list]:
    with gzip.open(shots_path(season), "rt") as f:
        return json.load(f)


def opening_day(season: int) -> date:
    first = min(s[0] for s in load_shots(season))
    return datetime.strptime(str(first), "%Y%m%d").date()


def dash_path(season: int, tag: str) -> Path:
    return NBA_DIR / f"dash_{season}_{tag}.json"


def load_dash(season: int, tag: str) -> dict[int, dict]:
    return {int(k): v for k, v in json.loads(dash_path(season, tag).read_text()).items()}


def save_dash(season: int, tag: str, dash: dict[int, dict]) -> None:
    dash_path(season, tag).write_text(json.dumps(dash))


def build_id_map(seasons: list[int]) -> dict[str, str]:
    """NBA.com id -> ESPN id over every downloaded season, matched within each season by name and team."""
    ds = load_history()
    path = HISTORY_DIR / "nba_ids.json"
    ids: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    for season in seasons:
        espn = {}
        for pid, lines in ds.lines.items():
            line = next((ln for ln in lines if ln.season == season), None)
            if line:
                espn[pid] = (ds.names.get(pid, ""), ESPN_TO_NBA_TEAM.get(line.team, line.team))
        for (pid, s) in ds.espn:  # projected-only rows (the season in progress)
            if s == season and pid not in espn:
                espn[pid] = (ds.names.get(pid, ""), "")
        if not dash_path(season, "full").exists():
            continue
        nba = {nid: (v["name"], v["team"]) for nid, v in load_dash(season, "full").items()}
        found = match_ids({n: v for n, v in nba.items() if str(n) not in ids}, espn)
        ids.update({str(k): v for k, v in found.items()})
        missing = [v[0] for n, v in nba.items() if str(n) not in ids and v[0]]
        print(f"  {season}: {len(nba) - len(missing)}/{len(nba)} NBA.com players linked to ESPN"
              + (f"; unlinked e.g. {', '.join(missing[:6])}" if missing else ""))
    path.write_text(json.dumps(dict(sorted(ids.items())), indent=0))
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2021)
    ap.add_argument("--last", type=int, default=2027)
    ap.add_argument("--cutoffs", type=int, nargs="*", default=[2024, 2025, 2026],
                    help="seasons to also fetch as of each in-season cutoff (for the backtest)")
    ap.add_argument("--refresh", action="store_true", help="re-download files that already exist")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # player names with accents on a cp1252 console
    NBA_DIR.mkdir(parents=True, exist_ok=True)
    client = NbaStatsClient()
    seasons = list(range(args.first, args.last + 1))
    today = date.today()
    for season in seasons:
        in_progress = date(season - 1, 10, 1) <= today <= date(season, 4, 30)
        if args.refresh or in_progress or not shots_path(season).exists():
            rows = client.season_shots(season, through=today if in_progress else None)
            with gzip.open(shots_path(season), "wt") as f:
                json.dump([compact_shot(r) for r in rows], f)
            print(f"{season}: {len(rows)} shots", flush=True)
        if args.refresh or in_progress or not dash_path(season, "full").exists():
            dash = client.dashboards(season)
            save_dash(season, "full", dash)
            print(f"{season}: dashboards for {len(dash)} players", flush=True)
        if season in args.cutoffs:
            opening = opening_day(season)
            for days in CUTOFF_DAYS:
                if args.refresh or not dash_path(season, f"d{days}").exists():
                    save_dash(season, f"d{days}", client.dashboards(season, opening + timedelta(days=days - 1)))
            print(f"{season}: in-season dashboards through days {', '.join(map(str, CUTOFF_DAYS))} "
                  f"after {opening:%b %d}", flush=True)
    client.close()
    build_id_map(seasons)


if __name__ == "__main__":
    main()
