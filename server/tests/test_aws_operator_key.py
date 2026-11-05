"""scripts.aws_operator_key: what the key may do, and that saving it leaves everything else alone (offline)."""

from __future__ import annotations

import configparser

import pytest

pytest.importorskip("boto3")

from scripts import aws_operator_key as K  # noqa: E402

PUBLISHER = "[courtside-publisher]\naws_access_key_id = PUB\naws_secret_access_key = PUBSECRET\n"


def _actions(doc: dict) -> list[str]:
    out = []
    for st in doc["Statement"]:
        out.extend([st["Action"]] if isinstance(st["Action"], str) else st["Action"])
    return out


def test_the_policy_stays_inside_the_sims():
    doc = K.policy("123456789012")
    actions = _actions(doc)
    assert all("*" not in a for a in actions)
    assert not [a for a in actions if a.startswith("iam:") or "delete" in a.lower()]
    assert {a.split(":")[0] for a in actions} == {"s3", "lambda", "logs"}
    assert all(st["Effect"] == "Allow" for st in doc["Statement"])
    by_sid = {st["Sid"]: st for st in doc["Statement"]}
    assert by_sid["SimsFiles"]["Resource"] == "arn:aws:s3:::courtside-123456789012-us-east-2-an/sims/*"
    assert by_sid["ListSimsFiles"]["Condition"] == {"StringLike": {"s3:prefix": "sims/*"}}
    for arn in by_sid["RunAndUpdateSimsFunctions"]["Resource"]:
        assert arn.split(":function:")[1].split(":")[0] in K.FUNCTIONS


def test_a_new_section_leaves_the_others_alone():
    out = K.replace_section(PUBLISHER, "new", ["a = 1"])
    assert out == PUBLISHER + "\n[new]\na = 1\n"
    assert K.replace_section("", "new", ["a = 1"]) == "[new]\na = 1\n"


def test_replacing_a_section_keeps_what_surrounds_it():
    text = "[one]\nx = 1\n\n[new]\na = old\nb = old\n\n[two]\ny = 2\n"
    out = K.replace_section(text, "new", ["a = 1"])
    parsed = configparser.ConfigParser()
    parsed.read_string(out)
    assert dict(parsed["one"]) == {"x": "1"}
    assert dict(parsed["two"]) == {"y": "2"}
    assert dict(parsed["new"]) == {"a": "1"}
    assert "old" not in out


def test_windows_line_endings_stay():
    out = K.replace_section("[one]\r\nx = 1\r\n", "new", ["a = 1"])
    assert out == "[one]\r\nx = 1\r\n\r\n[new]\r\na = 1\r\n"


def test_saving_leaves_only_the_finished_file(tmp_path):
    path = tmp_path / "credentials"
    path.write_text(PUBLISHER, encoding="utf-8")
    K.write_section(path, K.USER, ["aws_access_key_id = ID", "aws_secret_access_key = SECRET"])
    K.write_section(path, K.USER, ["aws_access_key_id = ID2", "aws_secret_access_key = SECRET2"])
    parsed = configparser.ConfigParser()
    parsed.read(path)
    assert parsed["courtside-publisher"]["aws_secret_access_key"] == "PUBSECRET"
    assert parsed[K.USER]["aws_access_key_id"] == "ID2"
    assert [f.name for f in tmp_path.iterdir()] == ["credentials"]
