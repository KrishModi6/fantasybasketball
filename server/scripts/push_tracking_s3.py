"""Copy the NBA.com tracking cache and the xFP model to the Courtside S3 bucket (docs/AWS.md).

Run it on the home machine right after scripts.fetch_tracking. stats.nba.com blocks cloud hosts, so
the data has to be fetched at home; the bucket is the off-machine, versioned copy.

Uploads (keys mirror server/data/):
  cache/nba/shots_<season>.json.gz, cache/nba/dash_<season>_<tag>.json
  model/xfp_model.joblib, model/xfp_metrics.json
Skips live_<season>.json, a six-hour cache the app rebuilds on every sync.

Each object carries its SHA-256 in metadata; files whose hash matches the copy in S3 are not sent
again, so the bucket's versions only grow when the data really changed.

Credentials come from `aws login --profile courtside` (needs boto3[crt], in requirements-dev.txt).
The bucket defaults to courtside-<account id>-<region>-an, the name created in docs/AWS.md.

Usage: python -m scripts.push_tracking_s3 [--dry-run] [--bucket NAME] [--profile courtside]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fbb.history import CACHE_DIR  # noqa: E402
from fbb.xfp import MODEL_PATH  # noqa: E402

CONTENT_TYPES = {".gz": "application/gzip", ".json": "application/json"}
EXPIRED = ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId", "InvalidToken")


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
    args = ap.parse_args()

    files = tracking_files()
    if not files:
        print(f"Nothing to upload: no tracking files under {CACHE_DIR / 'nba'}. Run scripts.fetch_tracking first.")
        return 1
    try:
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        bucket = args.bucket or default_bucket(session)
        print(f"s3://{bucket}: checking {len(files)} files", flush=True)
        sent, same = push(session.client("s3"), bucket, files, dry_run=args.dry_run)
    except (BotoCoreError, ClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        # Credential problems surface as BotoCoreError (no or unreadable login) or an expired-token code.
        if isinstance(exc, BotoCoreError) or exc.response.get("Error", {}).get("Code") in EXPIRED:
            print(f"Sign in again with: aws login --profile {args.profile}", file=sys.stderr)
        return 1
    print(f"{len(sent)} {'to upload' if args.dry_run else 'uploaded'}, {same} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
