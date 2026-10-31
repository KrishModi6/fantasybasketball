"""Copy the NBA.com tracking cache and the xFP model to the Courtside S3 bucket (docs/AWS.md).

Run it on the home machine. stats.nba.com blocks cloud hosts, so the data has to be fetched at home.

Backup (the default), right after scripts.fetch_tracking; keys mirror server/data/:
  cache/nba/shots_<season>.json.gz, cache/nba/dash_<season>_<tag>.json
  model/xfp_model.joblib, model/xfp_metrics.json
Skips live_<season>.json, a six-hour cache the app rebuilds on every sync. Each object carries its
SHA-256 in metadata; files whose hash matches the copy in S3 are not sent again, so the bucket's
versions only grow when the data really changed.

--live, once a day (the scheduled task in docs/AWS.md): brings this season's live file up to date from
NBA.com and uploads it gzipped to public/live_<season>.json.gz. CloudFront serves that folder, and every
Courtside app reads it before asking NBA.com itself (fbb.xfp.refresh_live).

Credentials come from `aws login --profile courtside` (needs boto3[crt], in requirements-dev.txt). The
scheduled task can't sign in every 12 hours, so it uses the courtside-publisher profile instead: an IAM
user's access key that may only write public/live_*.json.gz.
The bucket defaults to courtside-<account id>-<region>-an, the name created in docs/AWS.md.

Usage: python -m scripts.push_tracking_s3 [--live [--season 2027]] [--dry-run] [--bucket NAME]
       [--profile courtside]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import boto3
import botocore.session
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.history import CACHE_DIR  # noqa: E402
from fbb.xfp import MODEL_PATH, current_season, live_key, live_through, refresh_live  # noqa: E402

CONTENT_TYPES = {".gz": "application/gzip", ".json": "application/json"}
BAD_CREDENTIALS = ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "InvalidToken",
                   "InvalidAccessKeyId", "SignatureDoesNotMatch")
PUBLIC_MAX_AGE = 600  # seconds CloudFront and apps may reuse the published file before checking again


def tracking_files(cache_dir: Path = CACHE_DIR, model_path: Path = MODEL_PATH) -> list[tuple[Path, str]]:
    """(local file, S3 key) for every file that exists, in a stable order."""
    nba = cache_dir / "nba"
    files = [(p, f"cache/nba/{p.name}") for p in sorted([*nba.glob("shots_*.json.gz"), *nba.glob("dash_*.json")])]
    for p in (model_path, model_path.parent / "xfp_metrics.json"):
        if p.exists():
            files.append((p, f"model/{p.name}"))
    return files


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def remote_sha256(s3, bucket: str, key: str) -> str | None:
    """The hash stored with the current version of key, or None when there is no such object."""
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return head.get("Metadata", {}).get("sha256")


def push(s3, bucket: str, files: list[tuple[Path, str]], dry_run: bool = False) -> tuple[list[str], int]:
    """Upload files that are new or changed. Returns (keys uploaded, or that would be, count unchanged)."""
    sent, same = [], 0
    for path, key in files:
        digest = sha256(path)
        if remote_sha256(s3, bucket, key) == digest:
            same += 1
            continue
        if not dry_run:
            s3.upload_file(str(path), bucket, key, ExtraArgs={
                "ContentType": CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                "Metadata": {"sha256": digest},
            })
        sent.append(key)
        print(f"  {'would upload' if dry_run else 'uploaded'} {key} ({path.stat().st_size / 1e6:.2f} MB)", flush=True)
    return sent, same


def publish_live(s3, bucket: str, live: dict, dry_run: bool = False) -> str:
    """Upload a live file to public/, where CloudFront serves it. Returns the key."""
    key = f"public/{live_key(live['season'])}"
    body = gzip.compress(json.dumps({k: v for k, v in live.items() if k != "etag"}).encode())
    if not dry_run:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/gzip",
                      CacheControl=f"public, max-age={PUBLIC_MAX_AGE}")
    print(f"  {'would upload' if dry_run else 'uploaded'} {key} ({len(body) / 1e6:.2f} MB, through "
          f"{live['through']}, {len(live['dash'])} players, {len(live['shots'])} shots)", flush=True)
    return key


def credentials_hint(profile: str) -> str:
    """What to do when profile's credentials are missing or rejected. Running `aws login` on an
    access-key profile would turn it into a sign-in profile, so only sign-in profiles are told to."""
    try:
        signs_in = "login_session" in botocore.session.Session(profile=profile).get_scoped_config()
    except BotoCoreError:  # no such profile
        signs_in = False
    if signs_in:
        return f"Sign in again with: aws login --profile {profile}"
    return f"Profile {profile} has no working access key; replace it as docs/AWS.md (1b) describes"


def default_bucket(session: boto3.Session) -> str:
    account = session.client("sts").get_caller_identity()["Account"]
    return f"courtside-{account}-{session.region_name}-an"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "courtside"))
    ap.add_argument("--region", default="us-east-2")
    ap.add_argument("--bucket", default=os.environ.get("FBB_S3_BUCKET"),
                    help="defaults to courtside-<account id>-<region>-an")
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded, upload nothing")
    ap.add_argument("--live", action="store_true", help="publish this season's live file to public/")
    ap.add_argument("--season", type=int, default=current_season(), help="with --live; default: this season")
    args = ap.parse_args()

    files = tracking_files()
    if not args.live and not files:
        print(f"Nothing to upload: no tracking files under {CACHE_DIR / 'nba'}. Run scripts.fetch_tracking first.")
        return 1
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        bucket = args.bucket or default_bucket(session)  # checks the sign-in before NBA.com is asked
        if args.live:
            try:
                live = refresh_live(args.season, cloud=False)
            except Exception as exc:  # noqa: BLE001 - any NBA.com failure means there's nothing new to publish
                print(f"NBA.com unavailable ({exc}); nothing published", file=sys.stderr)
                return 1
            if live["through"] != live_through():
                print(f"NBA.com unavailable: the newest data is through {live['through']}; nothing published",
                      file=sys.stderr)
                return 1
            print(f"s3://{bucket}: publishing season {args.season}", flush=True)
            publish_live(session.client("s3"), bucket, live, dry_run=args.dry_run)
            return 0
        print(f"s3://{bucket}: checking {len(files)} files", flush=True)
        sent, same = push(session.client("s3"), bucket, files, dry_run=args.dry_run)
    except (BotoCoreError, ClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        # Credential problems surface as BotoCoreError (no or unreadable login) or a rejected-key code.
        if isinstance(exc, BotoCoreError) or exc.response.get("Error", {}).get("Code") in BAD_CREDENTIALS:
            print(credentials_hint(args.profile), file=sys.stderr)
        return 1
    print(f"{len(sent)} {'to upload' if args.dry_run else 'uploaded'}, {same} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
