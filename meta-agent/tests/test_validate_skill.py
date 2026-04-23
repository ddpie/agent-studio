"""Tests for validate_skill — static SKILL.md + requires: check."""
import json
import sys
import types
from unittest.mock import MagicMock, patch


_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "MODEL_ID": "mock",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/t",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config

_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands


def _stub_s3(*, index: list, skill_md: bytes, files: list):
    s3 = MagicMock()

    def _get_object(Bucket, Key):
        if Key == "skills/index.json":
            return {"Body": MagicMock(read=lambda: json.dumps(index).encode())}
        if Key.endswith("/SKILL.md"):
            return {"Body": MagicMock(read=lambda: skill_md)}
        raise KeyError(Key)

    s3.get_object.side_effect = _get_object

    # Paginator returning the given file list under skills/{id}/
    paginator = MagicMock()
    pages = [{"Contents": [{"Key": f"skills/abc/{f}"} for f in files]}]
    paginator.paginate.return_value = iter(pages)
    s3.get_paginator.return_value = paginator
    return s3


def test_validate_skill_returns_error_for_unknown_name():
    from tools.validate_skill import validate_skill

    s3 = _stub_s3(index=[], skill_md=b"", files=[])
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("no-such"))
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_validate_skill_passes_on_valid_frontmatter_with_pkg():
    md = b"""---
name: "ok-skill"
description: "does X"
requires:
  - type: package
    name: numpy
  - type: asset
    path: scripts/run.py
---
# body
"""
    index = [{"id": "abc", "name": "ok-skill"}]
    s3 = _stub_s3(index=index, skill_md=md, files=["SKILL.md", "scripts/run.py"])
    from tools.validate_skill import validate_skill
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("ok-skill"))
    assert out["ok"] is True
    assert out["missing_fields"] == []
    pkg_req = next(r for r in out["requirements"] if r["type"] == "package")
    assert pkg_req["ok"] is True
    asset_req = next(r for r in out["requirements"] if r["type"] == "asset")
    assert asset_req["ok"] is True


def test_validate_skill_flags_missing_asset():
    md = b"""---
name: "skill-bad"
description: "desc"
requires:
  - type: asset
    path: scripts/missing.py
---
body
"""
    index = [{"id": "abc", "name": "skill-bad"}]
    s3 = _stub_s3(index=index, skill_md=md, files=["SKILL.md", "scripts/present.py"])
    from tools.validate_skill import validate_skill
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("skill-bad"))
    assert out["ok"] is False
    asset = out["requirements"][0]
    assert asset["ok"] is False
    assert "missing.py" in asset["note"]


def test_validate_skill_flags_uncertain_package():
    md = b"""---
name: "unknown-pkg"
description: "d"
requires:
  - type: package
    name: some-obscure-lib
---
"""
    index = [{"id": "abc", "name": "unknown-pkg"}]
    s3 = _stub_s3(index=index, skill_md=md, files=["SKILL.md"])
    from tools.validate_skill import validate_skill
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("unknown-pkg"))
    req = out["requirements"][0]
    assert req["ok"] is False
    assert "not in known CI pre-installed set" in req["note"]
    assert "pip install" in req["suggestion"]


def test_validate_skill_rejects_non_list_requires():
    md = b"""---
name: "bad-req"
description: "d"
requires: "not a list"
---
"""
    index = [{"id": "abc", "name": "bad-req"}]
    s3 = _stub_s3(index=index, skill_md=md, files=["SKILL.md"])
    from tools.validate_skill import validate_skill
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("bad-req"))
    assert out["ok"] is False
    assert any("must be a list" in r.get("note", "") for r in out["requirements"])


def test_validate_skill_reports_missing_description():
    md = b"""---
name: "incomplete"
---
"""
    index = [{"id": "abc", "name": "incomplete"}]
    s3 = _stub_s3(index=index, skill_md=md, files=["SKILL.md"])
    from tools.validate_skill import validate_skill
    with patch("boto3.client", return_value=s3):
        out = json.loads(validate_skill("incomplete"))
    assert "description" in out["missing_fields"]
    assert out["ok"] is False
