"""This season's luck board: fantasy points per game so far against xFP, from NBA.com.

Players well above their xFP are sell-high candidates, players well below are buy-lows. The columns split
the gap by where it came from. Needs the trained bundle (python -m scripts.backtest_xfp) and a home
connection (stats.nba.com does not answer cloud hosts).

Usage: python -m scripts.xfp_report --season 2027 --min-minutes 150 --top 15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.models import ScoringSettings  # noqa: E402
from fbb.xfp import XfpModel, calibrate, calibrate_lines, refresh_live, shot_totals, tracking_line  # noqa: E402

SOURCES = {"shooting": ("PTS", "FGM", "3PM", "FTM"), "AST": ("AST",), "REB": ("REB",), "STL": ("STL",),
           "BLK": ("BLK",)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2027)
    ap.add_argument("--min-minutes", type=float, default=150)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    model = XfpModel.load()
    if model is None:
        sys.exit("No xFP model. Run: python -m scripts.backtest_xfp")
    live = refresh_live(args.season)
    shots = live["shots"]
    totals = shot_totals(shots, calibrate(shots, model.shot_model.predict(shots)))
    scoring = ScoringSettings()
    rows = []
    lines = calibrate_lines({int(nid): tracking_line(args.season, row, totals.get(int(nid)), model.skill(int(nid), args.season))
                             for nid, row in live["dash"].items() if row["MIN"]})
    for nid, row in live["dash"].items():
        if row["MIN"] < args.min_minutes or int(nid) not in lines:
            continue
        line = lines[int(nid)]
        diff = {k: line.actual.get(k, 0) - line.expected.get(k, 0) for k in line.actual}
        split = {s: scoring.fantasy_points({k: diff[k] for k in keys}) / line.gp for s, keys in SOURCES.items()}
        fp, xfp = scoring.fantasy_points(line.actual) / line.gp, scoring.fantasy_points(line.expected) / line.gp
        rows.append((fp - xfp, row["name"], row["team"], int(line.gp), fp, xfp, split))
    rows.sort(key=lambda r: -r[0])

    head = f"{'player':<26}{'team':<5}{'GP':>4}{'FP/G':>7}{'xFP/G':>7}{'luck':>7}" + "".join(f"{s:>10}" for s in SOURCES)
    for title, picks in ((f"Running hot (sell high), through {live['through']}", rows[:args.top]),
                         ("Running cold (buy low)", rows[::-1][:args.top])):
        print(f"\n{title}\n{head}")
        for luck, name, team, gp, fp, xfp, split in picks:
            print(f"{name[:25]:<26}{team:<5}{gp:>4}{fp:>7.1f}{xfp:>7.1f}{luck:>+7.1f}"
                  + "".join(f"{split[s]:>+10.1f}" for s in SOURCES))
    print(f"\n{len(rows)} players with {args.min_minutes:.0f}+ minutes. Luck = FP/G minus xFP/G (ESPN default "
          f"points); the columns split it by stat.")


if __name__ == "__main__":
    main()
