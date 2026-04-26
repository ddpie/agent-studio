"""Regression tests for list_mcp_target_tools fuzzy matching.

Protects against the AWSCloudOpsAssistant regression: Meta-Agent asked for
target_name="cloudwatch" but the fuzzy matcher picked mcp-cloudwatch-
applicationsignals.json (longer, alphabetically first in some S3 listings),
leading the agent prompt to reference tools like audit_services that don't
exist on the mcp_cloudwatch runtime the agent actually binds to.
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock


_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {"REGION": "us-east-1", "S3_BUCKET": "test-bucket"}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _make_s3_mock(manifest_names: list[str], tools_by_manifest: dict):
    s3 = MagicMock()
    s3.list_objects_v2.return_value = {
        "Contents": [{"Key": f"mcp/target-tools/{n}.json"} for n in manifest_names]
    }
    def _get(Bucket, Key):
        name = Key.split("/")[-1].removesuffix(".json")
        body = json.dumps(tools_by_manifest.get(name, [])).encode()
        return {"Body": MagicMock(read=lambda: body)}
    s3.get_object.side_effect = _get
    return s3


def test_cloudwatch_prefers_short_match_not_applicationsignals(monkeypatch):
    """Regression guard for the AWSCloudOpsAssistant root cause."""
    import boto3
    from tools import list_mcp_target_tools as mod
    s3 = _make_s3_mock(
        ["mcp-cloudwatch-applicationsignals", "mcp-cloudwatch", "mcp-cloudtrail"],
        {
            "mcp-cloudwatch-applicationsignals": [{"name": "audit_services", "description": "..."}],
            "mcp-cloudwatch": [{"name": "get_active_alarms", "description": "..."}],
        },
    )
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: s3)
    out = json.loads(mod.list_mcp_target_tools("cloudwatch"))
    # Must resolve to the plain cloudwatch manifest, not applicationsignals
    assert out["manifest_key"] == "mcp-cloudwatch.json"
    assert any(t["name"] == "get_active_alarms" for t in out["tools"])
    assert not any(t["name"] == "audit_services" for t in out["tools"])


def test_applicationsignals_exact_name_still_works(monkeypatch):
    """Users asking specifically for the applicationsignals manifest get it."""
    import boto3
    from tools import list_mcp_target_tools as mod
    s3 = _make_s3_mock(
        ["mcp-cloudwatch-applicationsignals", "mcp-cloudwatch"],
        {
            "mcp-cloudwatch-applicationsignals": [{"name": "audit_services", "description": "..."}],
            "mcp-cloudwatch": [{"name": "get_active_alarms", "description": "..."}],
        },
    )
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: s3)
    out = json.loads(mod.list_mcp_target_tools("cloudwatch-applicationsignals"))
    assert out["manifest_key"] == "mcp-cloudwatch-applicationsignals.json"
    assert any(t["name"] == "audit_services" for t in out["tools"])


def test_hyphen_normalization_still_works(monkeypatch):
    """'cloudtrail' should still match 'mcp-cloudtrail.json'."""
    import boto3
    from tools import list_mcp_target_tools as mod
    s3 = _make_s3_mock(
        ["mcp-cloudtrail"],
        {"mcp-cloudtrail": [{"name": "LookupEvents", "description": "..."}]},
    )
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: s3)
    out = json.loads(mod.list_mcp_target_tools("cloudtrail"))
    assert out["manifest_key"] == "mcp-cloudtrail.json"


def test_unknown_target_returns_empty_with_hint(monkeypatch):
    """When no match, return an empty tools list + available_targets for recovery."""
    import boto3
    from tools import list_mcp_target_tools as mod
    s3 = _make_s3_mock(["mcp-iam"], {"mcp-iam": []})
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: s3)
    out = json.loads(mod.list_mcp_target_tools("does-not-exist"))
    assert out["tool_count"] == 0
    assert out["tools"] == []
    assert "available_targets" in out
