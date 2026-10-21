"""Fetch recent game logs for a completed season (used to estimate stat overdispersion).

Usage: python -m scripts.fetch_game_logs --season 2026 --games 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.connectors.espn import EspnClient, parse_player  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--limit", type=int, default=350)
    args = ap.parse_args()
    client = EspnClient()
    raw = client.player_pool(args.season, limit=args.limit, recent_games=args.games)
    out = {}
    for entry in raw:
        p = parse_player(entry, args.season)
        logs = [g.model_dump() for g in p.recent_games if g.season == args.season]
        if len(logs) >= 10:
            out[p.id] = logs
    path = ROOT / "data" / "history" / f"game_logs_{args.season}.json"
    path.write_text(json.dumps(out))
    print(f"wrote logs for {len(out)} players -> {path}")


if __name__ == "__main__":
    main()
