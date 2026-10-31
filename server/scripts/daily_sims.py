"""Daily check of the matchup simulator against this season's finished weeks (docs/AWS.md, section 4).

Run every morning by the scheduled task in docs/AWS.md, section 1b, after the tracking feed:
  1. rebuild this season's projections week by week from ESPN's games so far, the way
     scripts/backtest_sims.py does for past seasons (ESPN and LightGBM, so at home);
  2. upload them to s3://<bucket>/sims/inputs/;
  3. have Lambda play LEAGUES synthetic leagues through every finished week with the app's settings as
     shipped, a few leagues per invocation, all at once;
  4. have Lambda score them (win odds against who won, 80% ranges against the real scores) into
     sims/daily/<season>/<day>.json and latest.json.
Before the season's first matchup week ends there is nothing to score, and it stops after step 1. The
numbers move when a week ends; on other days the run repeats the same weeks.

Usage: python -m scripts.daily_sims [--season 2027] [--leagues 32] [--profile courtside]
"""

from __future__ import annotations

import argparse
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
LEAGUES = 32  # they share the same real box scores; more leagues only smooth out drafts and rosters
PER_INVOCATION = 2  # one process per league: 3,008 MB of Lambda is about 1.7 vCPUs
WAIT = 14 * 60  # seconds to wait for the leagues; an invocation stops at 15 minutes


def _finished(s3, bucket: str, run: str) -> int:
    return s3.list_objects_v2(Bucket=bucket, Prefix=f"sims/results/{run}/").get("KeyCount", 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "courtside"))
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--bucket", default=os.environ.get("FBB_S3_BUCKET"),
                    help="defaults to courtside-<account id>-<region>-an")
    ap.add_argument("--season", type=int, default=current_season())
    ap.add_argument("--leagues", type=int, default=LEAGUES)
    args = ap.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        bucket = args.bucket or default_bucket(session)  # checks the sign-in before ESPN is asked
        path = B.prepare(args.season, live=True)
        if path is None:
            print(f"Season {args.season}: no matchup week has finished yet; nothing to check")
            return 0
        day = datetime.now(EASTERN).date().isoformat()
        run = f"daily-{args.season}-{day}"
        s3 = session.client("s3")
        lam = session.client("lambda", config=Config(read_timeout=900, retries={"max_attempts": 0}))
        s3.upload_file(str(path), bucket, f"sims/inputs/{path.name}")
        leagues = list(range(args.leagues))
        for i in range(0, len(leagues), PER_INVOCATION):
            tasks = [[args.season, lg, B.APP_GROUP] for lg in leagues[i:i + PER_INVOCATION]]
            lam.invoke(FunctionName=FUNCTION, InvocationType="Event", Payload=json.dumps({"run": run, "tasks": tasks}))
        print(f"s3://{bucket}: {len(leagues)} leagues of season {args.season} sent to Lambda as {run}", flush=True)
        deadline = time.time() + WAIT
        while (done := _finished(s3, bucket, run)) < len(leagues):
            if time.time() > deadline:
                print(f"Only {done} of {len(leagues)} leagues finished in time; see CloudWatch log group "
                      f"/aws/lambda/{FUNCTION}", file=sys.stderr)
                return 1
            time.sleep(15)
        resp = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(
            {"run": run, "action": "report", "season": args.season, "day": day}))
        out = json.loads(resp["Payload"].read())
        if resp.get("FunctionError"):
            print(f"Report failed: {out}", file=sys.stderr)
            return 1
    except (BotoCoreError, ClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if isinstance(exc, BotoCoreError) or exc.response.get("Error", {}).get("Code") in BAD_CREDENTIALS:
            print(credentials_hint(args.profile), file=sys.stderr)
        return 1
    print(f"  weeks 1-{out['weeks']}, {out['matchups']} matchups: Brier {out['brier']:.3f}, log loss "
          f"{out['logloss']:.3f}, 80% ranges held {out['range80']:.0%}, scores {out['bias_pct']:+.1%} vs projected; "
          f"sims/daily/{args.season}/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
