"""scripts.push_tracking_s3: which files go to S3 and when they're skipped (offline, fake S3 client)."""

from __future__ import annotations

import gzip
import json

import pytest

pytest.importorskip("boto3")  # requirements-dev.txt

from botocore.exceptions import ClientError  # noqa: E402

from scripts import push_tracking_s3 as P  # noqa: E402

ALL_KEYS = ["cache/nba/dash_2026_d7.json", "cache/nba/dash_2026_full.json", "cache/nba/shots_2026.json.gz",
            "model/xfp_model.joblib", "model/xfp_metrics.json"]


class FakeS3:
    def __init__(self):
        self.objects: dict[str, dict] = {}  # key -> metadata
        self.uploads: list[tuple[str, str]] = []  # (key, content type)
        self.puts: list[dict] = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"Metadata": self.objects[Key]}

    def upload_file(self, Filename, Bucket, Key, ExtraArgs):
        self.uploads.append((Key, ExtraArgs["ContentType"]))
        self.objects[Key] = ExtraArgs["Metadata"]

    def put_object(self, **kw):
        self.puts.append(kw)


class DeniedS3(FakeS3):
    def head_object(self, Bucket, Key):
        raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")


@pytest.fixture
def data(tmp_path):
    nba = tmp_path / "cache" / "nba"
    nba.mkdir(parents=True)
    (nba / "shots_2026.json.gz").write_bytes(b"\x1f\x8bshots")
    (nba / "dash_2026_full.json").write_text("{}")
    (nba / "dash_2026_d7.json").write_text("{}")
    (nba / "live_2027.json").write_text("{}")
    model = tmp_path / "model" / "xfp_model.joblib"
    model.parent.mkdir()
    model.write_bytes(b"model")
    (model.parent / "xfp_metrics.json").write_text("{}")
    (model.parent / "projection_model.joblib").write_bytes(b"not part of xFP")
    return tmp_path / "cache", model


def test_files_mirror_the_data_dir_and_skip_the_live_cache(data):
    assert [key for _, key in P.tracking_files(*data)] == ALL_KEYS


def test_new_files_upload_with_content_type_and_hash(data):
    s3 = FakeS3()
    sent, same = P.push(s3, "bucket", P.tracking_files(*data))
    assert (sent, same) == (ALL_KEYS, 0)
    types = dict(s3.uploads)
    assert types["cache/nba/shots_2026.json.gz"] == "application/gzip"
    assert types["cache/nba/dash_2026_full.json"] == "application/json"
    assert types["model/xfp_model.joblib"] == "application/octet-stream"
    assert len(s3.objects["model/xfp_model.joblib"]["sha256"]) == 64


def test_only_changed_files_upload_again(data):
    s3 = FakeS3()
    files = P.tracking_files(*data)
    P.push(s3, "bucket", files)
    assert P.push(s3, "bucket", files) == ([], 5)
    (data[0] / "nba" / "dash_2026_full.json").write_text('{"203999": {}}')
    assert P.push(s3, "bucket", files) == (["cache/nba/dash_2026_full.json"], 4)


def test_dry_run_uploads_nothing(data):
    s3 = FakeS3()
    sent, _ = P.push(s3, "bucket", P.tracking_files(*data), dry_run=True)
    assert sent == ALL_KEYS and s3.uploads == []


def test_errors_other_than_missing_object_propagate(data):
    with pytest.raises(ClientError):
        P.push(DeniedS3(), "bucket", P.tracking_files(*data))


def test_live_file_is_published_gzipped_to_public_without_our_etag():
    s3 = FakeS3()
    live = {"season": 2027, "through": "2026-10-29", "fetched": "2026-10-30T12:00:00+00:00", "dash": {"77": {}},
            "shots": [[20261029, 77]], "etag": '"v1"'}
    assert P.publish_live(s3, "bucket", live) == "public/live_2027.json.gz"
    put = s3.puts[0]
    assert put["Key"] == "public/live_2027.json.gz" and put["ContentType"] == "application/gzip"
    assert put["CacheControl"] == "public, max-age=600"
    assert json.loads(gzip.decompress(put["Body"])) == {k: v for k, v in live.items() if k != "etag"}
    P.publish_live(s3, "bucket", live, dry_run=True)
    assert len(s3.puts) == 1
