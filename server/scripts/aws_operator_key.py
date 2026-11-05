"""A permanent access key limited to the sims, so sims work doesn't wait for a browser sign-in.

`aws login` sessions end after 12 hours. This creates the IAM user courtside-sims-operator, whose one
inline policy covers the sims/ files, the two sims functions and their logs (docs/AWS.md, section 4), and
saves its key in your AWS credentials file under that profile name. The key is never printed. It's tested
first: one that can't do its job, or can do more than it should, is deleted again and nothing is saved.

Run it yourself, signed in with `aws login --profile courtside`:
  python scripts/aws_operator_key.py --dry-run   shows the policy, changes nothing
  python scripts/aws_operator_key.py             creates the user and its key
  python scripts/aws_operator_key.py --rotate    replaces the key, deleting the old one once the new one works
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

USER = "courtside-sims-operator"
POLICY = "run-courtside-sims"
SIGNED_IN_PROFILE = "courtside"
REGION = "us-east-2"
FUNCTIONS = ("courtside-sims", "courtside-daily-prep")
PROPAGATION_SECONDS = 120  # a new key and policy take a moment to reach every service


class Stop(Exception):
    """Why the script stopped, with nothing left half done."""


def bucket_name(account: str) -> str:
    return f"courtside-{account}-{REGION}-an"


def policy(account: str) -> dict:
    """No IAM, no deletes, nothing outside sims/ and the two functions. Overwritten files stay recoverable:
    the bucket is versioned. s3:GetObjectVersion is there because a redeploy names the package's version."""
    bucket = bucket_name(account)
    functions = [f"arn:aws:lambda:{REGION}:{account}:function:{name}" for name in FUNCTIONS]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "SimsFiles", "Effect": "Allow",
             "Action": ["s3:AbortMultipartUpload", "s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
             "Resource": f"arn:aws:s3:::{bucket}/sims/*"},
            {"Sid": "ListSimsFiles", "Effect": "Allow", "Action": "s3:ListBucket",
             "Resource": f"arn:aws:s3:::{bucket}", "Condition": {"StringLike": {"s3:prefix": "sims/*"}}},
            {"Sid": "RunAndUpdateSimsFunctions", "Effect": "Allow",
             "Action": ["lambda:GetFunction", "lambda:GetFunctionConfiguration", "lambda:InvokeFunction",
                        "lambda:ListVersionsByFunction", "lambda:PublishVersion", "lambda:UpdateFunctionCode"],
             "Resource": functions + [f"{arn}:*" for arn in functions]},
            {"Sid": "ReadSimsLogs", "Effect": "Allow",
             "Action": ["logs:DescribeLogStreams", "logs:FilterLogEvents", "logs:GetLogEvents"],
             "Resource": [f"arn:aws:logs:{REGION}:{account}:log-group:/aws/lambda/{name}:*" for name in FUNCTIONS]},
        ],
    }


def replace_section(text: str, header: str, lines: list[str]) -> str:
    """An AWS config or credentials file with ``[header]`` holding ``lines``, everything else as it was."""
    newline = "\r\n" if "\r\n" in text else "\n"
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("["):
            inside = line.strip() == f"[{header}]"
        if not inside:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return newline.join((kept + [""] if kept else []) + [f"[{header}]", *lines]) + newline


def write_section(path: Path, header: str, lines: list[str]) -> None:
    """Replaces the file in one step, so a failure can't leave it half written."""
    text = path.read_bytes().decode("utf-8") if path.exists() else ""
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(replace_section(text, header, lines))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _refused(call) -> bool:
    try:
        call()
    except ClientError as exc:
        return exc.response["Error"]["Code"] in ("AccessDenied", "AccessDeniedException")
    return False


def test_key(key: dict, account: str) -> None:
    """Raises Stop unless the key can do its job and is refused everything else tried here (reads only)."""
    session = boto3.Session(aws_access_key_id=key["AccessKeyId"], aws_secret_access_key=key["SecretAccessKey"],
                            region_name=REGION)
    lam, s3, iam = session.client("lambda"), session.client("s3"), session.client("iam")
    bucket = bucket_name(account)
    deadline = time.monotonic() + PROPAGATION_SECONDS
    while True:
        try:
            for name in FUNCTIONS:
                lam.get_function_configuration(FunctionName=name)
            s3.list_objects_v2(Bucket=bucket, Prefix="sims/", MaxKeys=1)
            break
        except ClientError as exc:
            if time.monotonic() > deadline:
                raise Stop(f"the new key still can't do its job: {exc}") from exc
            time.sleep(5)
    too_much = [what for what, call in (
        ("list IAM users", iam.list_users),
        ("list every function", lam.list_functions),
        ("list the public feed folder", lambda: s3.list_objects_v2(Bucket=bucket, Prefix="public/", MaxKeys=1)),
    ) if not _refused(call)]
    if too_much:
        raise Stop(f"the new key can do more than it should: {', '.join(too_much)}")


def setup(admin: boto3.Session, account: str, rotate: bool, aws_dir: Path) -> None:
    iam = admin.client("iam")
    try:
        iam.get_user(UserName=USER)
        print(f"User {USER} already exists.")
    except iam.exceptions.NoSuchEntityException:
        iam.create_user(UserName=USER, Tags=[{"Key": "project", "Value": "courtside"}])
        print(f"Created user {USER} (no console password).")
    iam.put_user_policy(UserName=USER, PolicyName=POLICY, PolicyDocument=json.dumps(policy(account)))
    print(f"Set its policy {POLICY}.")

    old = [k["AccessKeyId"] for k in iam.list_access_keys(UserName=USER)["AccessKeyMetadata"]]
    if old and not rotate:
        raise Stop(f"{USER} already has a key. To replace it, run this again with --rotate.")
    if len(old) > 1:
        raise Stop(f"{USER} already has two keys, the most a user can hold. Delete the one not in use first.")

    new = iam.create_access_key(UserName=USER)["AccessKey"]
    print(f"Testing the new key (up to {PROPAGATION_SECONDS // 60} minutes while it reaches every service)...")
    try:
        test_key(new, account)
        write_section(aws_dir / "config", f"profile {USER}", [f"region = {REGION}"])
        write_section(aws_dir / "credentials", USER, [f"aws_access_key_id = {new['AccessKeyId']}",
                                                      f"aws_secret_access_key = {new['SecretAccessKey']}"])
    except BaseException:
        iam.delete_access_key(UserName=USER, AccessKeyId=new["AccessKeyId"])
        print("Deleted the new key again; your credentials file still has what it had.")
        raise
    print(f"The key works and was refused what it should be. Saved in {aws_dir / 'credentials'} as [{USER}].")
    for key_id in old:
        iam.delete_access_key(UserName=USER, AccessKeyId=key_id)
        print("Deleted the old key.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Create or rotate the access key limited to the sims.")
    ap.add_argument("--dry-run", action="store_true", help="show the policy and change nothing")
    ap.add_argument("--rotate", action="store_true", help="replace the key")
    args = ap.parse_args()
    try:
        admin = boto3.Session(profile_name=SIGNED_IN_PROFILE, region_name=REGION)
        identity = admin.client("sts").get_caller_identity()
        print(f"Signed in with profile {SIGNED_IN_PROFILE}.")
        if args.dry_run:
            print(json.dumps(policy(identity["Account"]), indent=2))
            return 0
        setup(admin, identity["Account"], args.rotate, Path.home() / ".aws")
        print("Done.")
        return 0
    except (Stop, ClientError, BotoCoreError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        print(f"If that's about signing in, run: aws login --profile {SIGNED_IN_PROFILE}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
