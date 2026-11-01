"""AWS Lambda entry point for the daily check (scripts/daily_sims.py; docs/AWS.md, section 4).

EventBridge Scheduler starts the function courtside-daily-prep every morning. It does what
``python -m scripts.daily_sims`` does at home: rebuilds this season's projections from ESPN, sends the
leagues to courtside-sims, waits for them and has it write the report. ESPN answers requests from AWS;
stats.nba.com doesn't, which is why the xFP tracking feed (section 1b) stays on the home PC.

The package is read-only, so fbb's cache (FBB_CACHE_DIR) and the inputs go to /tmp. The past seasons'
game logs that prepare() reads (the absence table, last season's closing games) don't change, so they
live in s3://<bucket>/sims/cache/ rather than being fetched from ESPN every morning. This season's logs
are saved there after each run, so next season finds them.

Event: {} for this season, or {"season": 2027, "leagues": 32}
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

TMP = Path("/tmp")
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ.setdefault("FBB_CACHE_DIR", str(TMP / "cache"))

import boto3  # noqa: E402  (in the Lambda runtime)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BUCKET = os.environ.get("SIMS_BUCKET", "")
CACHE = "sims/cache/"
MARGIN = 90  # seconds left for the report once the leagues are in
PACKAGES = ("numpy", "scipy", "lightgbm", "joblib", "httpx", "pydantic")  # pinned in requirements.txt
LIBGOMP_DONOR = "scikit-learn==1.5.2"
MAX_UNZIPPED = 262_144_000  # Lambda's limit for a zip package


def handler(event, context):
    from fbb.history import CACHE_DIR
    from fbb.xfp import current_season
    from scripts import backtest_sims as B
    from scripts import daily_sims as D

    B.INPUTS = TMP / "sims"
    season = int(event.get("season") or current_season())
    s3 = boto3.client("s3")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    current = f"game_logs_{season}_full.json"
    uncached = []
    for name in sorted({f"game_logs_{s}_full.json" for s in (*B.LOG_SEASONS, season - 1)} - {current}):
        if not (CACHE_DIR / name).exists():
            try:
                s3.download_file(BUCKET, CACHE + name, str(CACHE_DIR / name))
            except s3.exceptions.ClientError:  # not saved yet: prepare() fetches it from ESPN
                uncached.append(name)
    deadline = time.time() + context.get_remaining_time_in_millis() / 1000 - MARGIN
    out = D.check(boto3.Session(), BUCKET, season, int(event.get("leagues", D.LEAGUES)), deadline)
    for name in (*uncached, current):
        if (CACHE_DIR / name).exists():
            s3.upload_file(str(CACHE_DIR / name), BUCKET, CACHE + name)
    return out or {"season": season, "weeks": 0}


def build(out: Path) -> None:
    """The deployment zip: fbb, the scripts the check runs, data/history and data/model, with the
    packages they need for Linux arm64 (Python 3.12) from PyPI's prebuilt wheels."""
    import shutil
    import subprocess
    import tempfile
    import zipfile

    reqs = [ln.split("#")[0].strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()]
    pins = [r for r in reqs if r.split("==")[0] in PACKAGES] + ["tzdata"]
    wheels = ["--platform", "manylinux2014_aarch64", "--platform", "manylinux_2_28_aarch64", "--implementation",
              "cp", "--python-version", "3.12", "--only-binary=:all:"]
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "pkg"
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", str(pkg), *wheels, *pins],
                       check=True)
        # LightGBM links GCC's OpenMP (libgomp.so.1), which the Lambda runtime doesn't have. scikit-learn's
        # wheel bundles a copy, and Lambda looks for libraries in /var/task/lib.
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--no-deps", "--dest", tmp, *wheels,
                        LIBGOMP_DONOR], check=True)
        with zipfile.ZipFile(next(Path(tmp).glob("scikit_learn-*.whl"))) as whl:
            (pkg / "lib").mkdir()
            (pkg / "lib" / "libgomp.so.1").write_bytes(whl.read(next(n for n in whl.namelist() if "/libgomp" in n)))
        for top in ("numpy", "scipy"):  # their test suites are 44 MB of the 250 MB allowed
            for tests in list((pkg / top).rglob("tests")):
                if tests.is_dir():
                    shutil.rmtree(tests)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(ROOT / "fbb", pkg / "fbb", ignore=ignore)
        for name in ("history", "model"):
            shutil.copytree(ROOT / "data" / name, pkg / "data" / name, ignore=ignore)
        (pkg / "scripts").mkdir()
        for name in ("__init__.py", "backtest_sims.py", "backtest_recency.py", "train_model.py", "daily_sims.py",
                     "daily_lambda.py", "push_tracking_s3.py"):
            shutil.copy(ROOT / "scripts" / name, pkg / "scripts" / name)
        files = [f for f in sorted(pkg.rglob("*")) if f.is_file() and "__pycache__" not in f.parts]
        size = sum(f.stat().st_size for f in files)
        if size > MAX_UNZIPPED:
            raise SystemExit(f"{size / 1e6:.0f} MB unzipped is over Lambda's {MAX_UNZIPPED / 1e6:.0f} MB")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files:
                z.write(f, f.relative_to(pkg))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {size / 1e6:.0f} MB unzipped)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("daily_lambda.zip"))
