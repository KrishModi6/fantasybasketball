"""Daily check of the matchup simulator against this season's finished weeks (docs/AWS.md, section 4).

Run every morning on AWS by the Lambda function courtside-daily-prep (scripts/daily_lambda.py), or by
hand from here:
  1. rebuild this season's projections week by week from ESPN's games so far, the way
     scripts/backtest_sims.py does for past seasons;
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


def check(session: boto3.Session, bucket: str, season: int, leagues: int = LEAGUES,
          deadline: float | None = None) -> dict | None:
    """Steps 1-4: the report's headline numbers, or None before the season's first week has ended.
    ``deadline`` (time.time()) to stop waiting for the leagues; WAIT after sending them by default."""
    path = B.prepare(season, live=True)
    if path is None:
        print(f"Season {season}: no matchup week has finished yet; nothing to check")
        return None
    day = datetime.now(EASTERN).date().isoformat()
    run = f"daily-{season}-{day}"
    s3 = session.client("s3")
    # Retries with backoff when Lambda is busy: the project runs at most 10 functions at once, and the
    # report can come while the league invocations still hold them (a second run on the same day).
    lam = session.client("lambda", config=Config(read_timeout=900, retries={"mode": "standard", "max_attempts": 6}))
    s3.upload_file(str(path), bucket, f"sims/inputs/{path.name}")
    for i in range(0, leagues, PER_INVOCATION):
        tasks = [[season, lg, B.APP_GROUP] for lg in range(i, min(i + PER_INVOCATION, leagues))]
        lam.invoke(FunctionName=FUNCTION, InvocationType="Event", Payload=json.dumps({"run": run, "tasks": tasks}))
    print(f"s3://{bucket}: {leagues} leagues of season {season} sent to Lambda as {run}", flush=True)
    deadline = deadline or time.time() + WAIT
    while (done := _finished(s3, bucket, run)) < leagues:
        if time.time() > deadline:
            raise RuntimeError(f"Only {done} of {leagues} leagues finished in time; see CloudWatch log group "
                               f"/aws/lambda/{FUNCTION}")
        time.sleep(15)
    resp = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(
        {"run": run, "action": "report", "season": season, "day": day}))
    out = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        raise RuntimeError(f"Report failed: {out}")
    print(f"  weeks 1-{out['weeks']}, {out['matchups']} matchups: Brier {out['brier']:.3f}, log loss "
          f"{out['logloss']:.3f}, 80% ranges held {out['range80']:.0%}, scores {out['bias_pct']:+.1%} vs projected; "
          f"sims/daily/{season}/latest.json")
    return out


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
