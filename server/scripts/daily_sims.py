"""Daily check of the matchup simulator against this season's finished weeks (docs/AWS.md, section 4).

Run at 7:30 AM and 6:30 PM US Eastern on AWS by the Lambda function courtside-daily-prep
(scripts/daily_lambda.py), or by hand from here:
  1. rebuild this season's projections week by week from ESPN's games so far, the way
     scripts/backtest_sims.py does for past seasons, and upload them to s3://<bucket>/sims/inputs/;
  2. score the pool of synthetic leagues already played through those weeks (win odds against who won,
     80% ranges against the real scores) into sims/daily/<season>/<day>.json and latest.json;
  3. add a new batch of leagues to the pool, sized so the runs spend about BUDGET_GBS of Lambda each.
     They run in the background, Lambda queueing what it can't run at once, and the next run scores them.
The pool belongs to the weeks finished (run daily-<season>-w<weeks>): every run until the next week ends adds
leagues the earlier ones didn't play, so the numbers get steadier through the week. When a week ends a new
pool starts, and its first FIRST_SLICE leagues are played and scored straight away. Before the season's first
matchup week ends there is nothing to score, and it stops after step 1.

Usage: python -m scripts.daily_sims [--season 2027] [--leagues 200] [--profile courtside]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.xfp import EASTERN, current_season  # noqa: E402
from scripts import backtest_sims as B  # noqa: E402
from scripts.push_tracking_s3 import BAD_CREDENTIALS, credentials_hint, default_bucket  # noqa: E402

FUNCTION = "courtside-sims"
# About $20 a month of Lambda at list price: 2 runs a day of 25,000 GB-seconds, $0.0000133334 per GB-second.
BUDGET_GBS = 25_000
# What one league costs per finished week at the app's 10,000 draws: 3,008 MB times its share of an
# invocation's billed time. Measured on courtside-sims on 2026-11-14 with 2 leagues an invocation in week 3:
# 6.7 cold, 6.1 warm (docs/AWS.md, section 4). Check it against real runs' REPORT lines now and then.
GBS_PER_LEAGUE_WEEK = 6.5
LEAGUES = 32  # the fewest a run adds
MAX_LEAGUES = 20_000  # the largest a pool grows, so its report stays well inside courtside-sims' 15 minutes
FIRST_SLICE = 64  # a new pool's leagues played and scored before the run's report
PER_INVOCATION = 2  # one process per league: 3,008 MB of Lambda is about 1.7 vCPUs
WAIT = 13 * 60  # seconds to wait for a new pool's first slice; an invocation stops at 15 minutes


def batch(weeks: int) -> int:
    """How many leagues a run adds, so a run costs about BUDGET_GBS whatever the week."""
    return max(LEAGUES, int(BUDGET_GBS / (GBS_PER_LEAGUE_WEEK * max(weeks, 1))))


def _played(s3, bucket: str, run: str) -> set[int]:
    """The leagues of a pool with results, from their file names (<season>_<league>_g<group>.json.gz)."""
    out = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"sims/results/{run}/"):
        out.update(int(o["Key"].rsplit("/", 1)[1].split("_")[1]) for o in page.get("Contents", []))
    return out


def _send(lam, run: str, season: int, leagues: range) -> None:
    for i in range(leagues.start, leagues.stop, PER_INVOCATION):
        tasks = [[season, lg, B.APP_GROUP] for lg in range(i, min(i + PER_INVOCATION, leagues.stop))]
        lam.invoke(FunctionName=FUNCTION, InvocationType="Event", Payload=json.dumps({"run": run, "tasks": tasks}))


def _report(lam, run: str, season: int, day: str) -> dict:
    resp = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps({"run": run, "action": "report", "season": season, "day": day}))
    out = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        raise RuntimeError(f"Report failed: {out}")
    print(f"  {out['leagues']} leagues, weeks 1-{out['weeks']}, {out['matchups']} matchups: Brier {out['brier']:.3f}, "
          f"log loss {out['logloss']:.3f}, 80% ranges held {out['range80']:.0%}, scores {out['bias_pct']:+.1%} vs "
          f"projected; sims/daily/{season}/latest.json", flush=True)
    return out


def check(session: boto3.Session, bucket: str, season: int, leagues: int | None = None,
          deadline: float | None = None) -> dict | None:
    """Steps 1-3: the report's headline numbers, or None before the season's first week has ended.
    ``leagues`` overrides how many a run adds; ``deadline`` (time.time()) stops the wait for a new pool's first slice."""
    path = B.prepare(season, live=True)
    if path is None:
        print(f"Season {season}: no matchup week has finished yet; nothing to check")
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        weeks = int(json.load(f)["weeks"])
    day = datetime.now(EASTERN).date().isoformat()
    run = f"daily-{season}-w{weeks}"
    s3 = session.client("s3")
    # Retries with backoff when Lambda is busy: the project runs at most 10 functions at once.
    lam = session.client("lambda", config=Config(read_timeout=900, retries={"mode": "standard", "max_attempts": 10}))
    s3.upload_file(str(path), bucket, f"sims/inputs/{path.name}")
    played = _played(s3, bucket, run)
    start = max(played, default=-1) + 1
    todo = range(start, min(start + (leagues or batch(weeks)), MAX_LEAGUES))
    if not played:  # a new pool: play its first leagues now, so this run has numbers to report
        first, todo = range(todo.start, min(todo.start + FIRST_SLICE, todo.stop)), range(min(todo.start + FIRST_SLICE, todo.stop), todo.stop)
        _send(lam, run, season, first)
        print(f"s3://{bucket}: new pool {run}, first {len(first)} leagues sent to Lambda", flush=True)
        deadline = deadline or time.time() + WAIT
        while len(_played(s3, bucket, run)) < len(first):
            if time.time() > deadline:
                raise RuntimeError(f"Only {len(_played(s3, bucket, run))} of {len(first)} leagues finished in time; "
                                   f"see CloudWatch log group /aws/lambda/{FUNCTION}")
            time.sleep(15)
    out = _report(lam, run, season, day)  # before the batch, so it never waits behind it for a Lambda slot
    _send(lam, run, season, todo)
    print(f"  {len(todo)} more leagues (from {todo.start}) sent to Lambda; the next run scores them", flush=True)
    return {**out, "added": len(todo)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "courtside"))
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--bucket", default=os.environ.get("FBB_S3_BUCKET"),
                    help="defaults to courtside-<account id>-<region>-an")
    ap.add_argument("--season", type=int, default=current_season())
    ap.add_argument("--leagues", type=int, help="how many leagues to add (default: what the budget allows)")
    args = ap.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        bucket = args.bucket or default_bucket(session)  # checks the sign-in before ESPN is asked
        check(session, bucket, args.season, args.leagues)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, BotoCoreError) or exc.response.get("Error", {}).get("Code") in BAD_CREDENTIALS:
            print(credentials_hint(args.profile), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
