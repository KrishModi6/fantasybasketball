"""The daily check's pool: budget-sized batches, numbered on from the last league, reported before the batch."""

from __future__ import annotations

import gzip
import io
import json

import pytest

from scripts import backtest_sims as B
from scripts import daily_sims as D


class FakeS3:
    def __init__(self, files: list[str]):
        self.files = list(files)
        self.uploaded = []

    def upload_file(self, path, bucket, key):
        self.uploaded.append(key)

    def get_paginator(self, _name):
        s3 = self

        class Pager:
            def paginate(self, Bucket, Prefix):
                return [{"Contents": [{"Key": k} for k in s3.files if k.startswith(Prefix)]}]
        return Pager()


class FakeLambda:
    def __init__(self, s3: FakeS3, run_done: bool = True):
        self.s3, self.run_done, self.calls = s3, run_done, []

    def invoke(self, FunctionName, Payload, InvocationType="RequestResponse"):
        body = json.loads(Payload)
        self.calls.append(("report",) if body.get("action") == "report" else ("tasks", [t[1] for t in body["tasks"]]))
        if body.get("action") == "report":
            out = {"leagues": 1, "weeks": 3, "matchups": 1, "brier": 0.2, "logloss": 0.6, "range80": 0.8, "bias_pct": 0}
            return {"Payload": io.BytesIO(json.dumps(out).encode())}
        if self.run_done:  # results land straight away
            self.s3.files += [f"sims/results/{body['run']}/2027_{t[1]}_g{t[2]}.json.gz" for t in body["tasks"]]
        return {"Payload": io.BytesIO(b"null")}


class FakeSession:
    def __init__(self, s3, lam):
        self._c = {"s3": s3, "lambda": lam}

    def client(self, name, **_kw):
        return self._c[name]


@pytest.fixture
def season(tmp_path, monkeypatch):
    path = tmp_path / "inputs_2027.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"weeks": 3}, f)
    monkeypatch.setattr(B, "prepare", lambda season, live=False: path)
    monkeypatch.setattr(D.time, "sleep", lambda _s: None)


def _sent(lam) -> list[int]:
    return [lg for c in lam.calls if c[0] == "tasks" for lg in c[1]]


def test_batch_is_sized_to_the_budget():
    assert D.batch(1) == int(D.BUDGET_GBS / D.GBS_PER_LEAGUE_WEEK)
    assert D.batch(19) < D.batch(3) < D.batch(1)
    assert D.batch(10_000) == D.LEAGUES


def test_a_new_pool_plays_its_first_slice_then_reports_then_sends_the_rest(season):
    s3 = FakeS3([])
    lam = FakeLambda(s3)
    out = D.check(FakeSession(s3, lam), "b", 2027, leagues=100)
    report = lam.calls.index(("report",))
    before = [lg for c in lam.calls[:report] for lg in c[1]]
    after = [lg for c in lam.calls[report + 1:] for lg in c[1]]
    assert before == list(range(D.FIRST_SLICE))
    assert after == list(range(D.FIRST_SLICE, 100))
    assert out["added"] == 100 - D.FIRST_SLICE
    assert s3.uploaded == ["sims/inputs/inputs_2027.json.gz"]


def test_a_pool_already_there_is_reported_first_and_grows_from_its_last_league(season):
    s3 = FakeS3([f"sims/results/daily-2027-w3/2027_{lg}_g{B.APP_GROUP}.json.gz" for lg in (0, 1, 5)])
    lam = FakeLambda(s3)
    D.check(FakeSession(s3, lam), "b", 2027, leagues=10)
    assert lam.calls[0] == ("report",)
    assert _sent(lam) == list(range(6, 16))
    assert all(len(c[1]) <= D.PER_INVOCATION for c in lam.calls if c[0] == "tasks")


def test_another_weeks_pool_doesnt_count(season):
    s3 = FakeS3([f"sims/results/daily-2027-w2/2027_{lg}_g{B.APP_GROUP}.json.gz" for lg in range(50)])
    lam = FakeLambda(s3)
    D.check(FakeSession(s3, lam), "b", 2027, leagues=70)
    assert _sent(lam) == list(range(70))  # w3 is a new pool


def test_a_pool_stops_growing_at_its_cap(season, monkeypatch):
    monkeypatch.setattr(D, "MAX_LEAGUES", 8)
    s3 = FakeS3([f"sims/results/daily-2027-w3/2027_{lg}_g{B.APP_GROUP}.json.gz" for lg in range(6)])
    lam = FakeLambda(s3)
    assert D.check(FakeSession(s3, lam), "b", 2027, leagues=50)["added"] == 2


def test_a_first_slice_that_doesnt_finish_fails_the_run(season):
    s3 = FakeS3([])
    lam = FakeLambda(s3, run_done=False)
    with pytest.raises(RuntimeError, match="finished in time"):
        D.check(FakeSession(s3, lam), "b", 2027, leagues=10, deadline=0.0001)
    assert ("report",) not in lam.calls
