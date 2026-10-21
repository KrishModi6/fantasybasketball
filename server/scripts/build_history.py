"""Download the historical player-season dataset used to train and backtest the model.

Writes:
  data/history/player_seasons.csv   one row per (season, player): actual totals + ESPN preseason projection
  data/history/athletes.json        player id -> {age (as of today), debut_year}

Usage:
  python -m scripts.build_history --first 2018 --last 2027
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.connectors.espn import EspnClient, parse_player  # noqa: E402
from fbb.constants import COUNTING_STATS  # noqa: E402

OUT = ROOT / "data" / "history"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2018)
    ap.add_argument("--last", type=int, default=2027)
    ap.add_argument("--limit", type=int, default=1100)
    ap.add_argument("--skip-ages", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    client = EspnClient()
    rows = []
    for season in range(args.first, args.last + 1):
        raw = client.player_pool(season, limit=args.limit, recent_games=1)
        n = 0
        for entry in raw:
            p = parse_player(entry, season)
            line = p.current
            espn = next((e for e in p.experts if e.season == season), None)
            if (line is None or line.gp == 0) and espn is None:
                continue
            row = {
                "season": season,
                "player_id": p.id,
                "name": p.name,
                "team": p.pro_team,
                "positions": "|".join(p.positions),
                "adp": p.adp if p.adp is not None else "",
                "gp": line.gp if line else 0,
                "min": line.minutes if line else 0,
            }
            for s in COUNTING_STATS:
                row[s] = (line.stats.get(s, 0.0) if line else 0.0)
            row["espn_gp"] = espn.gp if espn else ""
            row["espn_mpg"] = espn.minutes_per_game if espn else ""
            for s in COUNTING_STATS:
                row[f"espn_{s}"] = espn.per_game.get(s, "") if espn else ""
            rows.append(row)
            n += 1
        print(f"season {season}: {n} player rows", flush=True)

    path = OUT / "player_seasons.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")

    if args.skip_ages:
        return
    ids = sorted({r["player_id"] for r in rows if float(r["min"] or 0) >= 100 or r["espn_gp"] != ""})
    ages_path = OUT / "athletes.json"
    known = json.loads(ages_path.read_text()) if ages_path.exists() else {}
    todo = [i for i in ids if i not in known]
    print(f"fetching bios for {len(todo)} players ({len(known)} cached)")

    def bio(pid: str):
        try:
            a = client.athlete(pid).get("athlete", {})
            return pid, {"age": a.get("age"), "debut_year": a.get("debutYear")}
        except Exception as exc:  # retired players occasionally 404
            return pid, {"age": None, "debut_year": None, "error": str(exc)[:80]}

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for i, (pid, info) in enumerate(pool.map(bio, todo)):
            known[pid] = info
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                ages_path.write_text(json.dumps(known))
    ages_path.write_text(json.dumps(known, indent=0))
    print(f"wrote {len(known)} bios -> {ages_path}")


if __name__ == "__main__":
    main()
