"""AWS Lambda entry point for the simulator backtest (scripts/backtest_sims.py; docs/AWS.md, section 4).

Each invocation runs a few synthetic league-seasons side by side, one process per league (Lambda has
no /dev/shm, so multiprocessing.Pool can't be used; Process and Pipe work), and writes each league's
results to S3 next to the inputs the home machine uploaded. A league whose results are already there is
skipped, so an invocation can safely be sent again.

Event: {"run": "r1", "tasks": [[2026, 0, 0], [2026, 0, 1]]}  (season, league, config group)
       {"run": "r1", "action": "merge"}  -> one file with every league of the run
       {"run": "daily-2027-2026-11-01", "action": "report", "season": 2027, "day": "2026-11-01"}
           -> the daily check's summary, sims/daily/<season>/<day>.json and latest.json (scripts/daily_sims.py)

The simulator never touches the app's xFP model or ESPN client, so LightGBM, joblib and httpx aren't in
the package; empty stand-ins let fbb import without them.
"""

from __future__ import annotations

import gzip
import json
import multiprocessing
import os
import sys
import types
from pathlib import Path

if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    for _name in ("lightgbm", "joblib", "httpx"):
        sys.modules.setdefault(_name, types.ModuleType(_name))

import boto3  # noqa: E402  (in the Lambda runtime)

BUCKET = os.environ.get("SIMS_BUCKET", "")
PREFIX = os.environ.get("SIMS_PREFIX", "sims/")
TMP = Path("/tmp")


def _key(run: str, season: int, league: int, group: int) -> str:
    return f"{PREFIX}results/{run}/{season}_{league}_g{group}.json.gz"


def _work(season: int, league: int, group: int, run: str, conn) -> None:
    from scripts import backtest_sims as B

    try:
        B.INPUTS = TMP
        res = B.run_league(B.load_season(season), league, B.GROUPS[group])
        boto3.client("s3").put_object(Bucket=BUCKET, Key=_key(run, season, league, group),
                                      Body=gzip.compress(json.dumps(res).encode()))
        conn.send("ok")
    except Exception as exc:  # noqa: BLE001 - reported back to the handler
        conn.send(f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()


def _exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except s3.exceptions.ClientError:
        return False


def _results(s3, run: str) -> list[dict]:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=f"{PREFIX}results/{run}/"):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    return [json.loads(gzip.decompress(s3.get_object(Bucket=BUCKET, Key=k)["Body"].read())) for k in sorted(keys)]


def _merge(s3, run: str) -> dict:
    leagues = _results(s3, run)
    out = f"{PREFIX}merged/{run}.json.gz"
    s3.put_object(Bucket=BUCKET, Key=out, Body=gzip.compress(json.dumps(leagues).encode()))
    return {"merged": len(leagues), "key": out}


def _report(s3, run: str, season: int, day: str) -> dict:
    """The daily check: how the app's odds and ranges did over this season's finished weeks."""
    from scripts import backtest_sims as B

    s = B.summarize(_results(s3, run))
    w = s["weekly"]["app"]
    s["headline"] = {"day": day, "season": season, "leagues": s["leagues"], "weeks": len(w["by_week"]),
                     "matchups": w["matchups"], "brier": w["p_mc"]["brier"], "logloss": w["p_mc"]["logloss"],
                     "range80": w["range80"]["coverage"], "bias_pct": w["bias_pct"]}
    body = json.dumps(s, indent=1).encode()
    for name in (day, "latest"):
        s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}daily/{season}/{name}.json", Body=body,
                      ContentType="application/json")
    return s["headline"]


def handler(event, context):
    s3 = boto3.client("s3")
    run = event["run"]
    if event.get("action") == "merge":
        return _merge(s3, run)
    if event.get("action") == "report":
        return _report(s3, run, int(event["season"]), event["day"])
    tasks = [(int(s), int(lg), int(g)) for s, lg, g in event["tasks"] if not _exists(s3, _key(run, s, lg, g))]
    from scripts import backtest_sims as B

    names = {f"inputs_{season}{tag}.json.gz" for season, _, group in tasks for tag in B.input_tags(B.GROUPS[group])}
    for name in names:  # every time: a warm environment keeps /tmp, and this season's inputs change daily
        s3.download_file(BUCKET, f"{PREFIX}inputs/{name}", str(TMP / name))
    procs = []
    for season, league, group in tasks:
        parent, child = multiprocessing.Pipe(duplex=False)
        p = multiprocessing.Process(target=_work, args=(season, league, group, run, child))
        p.start()
        procs.append((season, league, group, p, parent))
    out = []
    for season, league, group, p, conn in procs:
        msg = conn.recv() if conn.poll(context.get_remaining_time_in_millis() / 1000 - 5) else "timed out"
        p.join(5)
        out.append({"season": season, "league": league, "group": group, "result": msg})
    print(json.dumps(out))
    if any(r["result"] != "ok" for r in out):
        raise RuntimeError(json.dumps(out))
    return out


def build(out: Path) -> None:
    """The deployment zip: fbb, this script and the backtest, with numpy and pydantic for Linux arm64
    (Python 3.12), straight from PyPI's prebuilt wheels."""
    import shutil
    import subprocess
    import tempfile
    import zipfile

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", tmp, "--platform",
                        "manylinux2014_aarch64", "--implementation", "cp", "--python-version", "3.12",
                        "--only-binary=:all:", "numpy==1.26.4", "pydantic==2.12.3", "tzdata"], check=True)
        shutil.copytree(root / "fbb", Path(tmp) / "fbb", ignore=shutil.ignore_patterns("__pycache__"))
        (Path(tmp) / "scripts").mkdir()
        for name in ("__init__.py", "backtest_sims.py", "sims_lambda.py"):
            shutil.copy(root / "scripts" / name, Path(tmp) / "scripts" / name)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(tmp).rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.relative_to(tmp))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sims_lambda.zip"))
