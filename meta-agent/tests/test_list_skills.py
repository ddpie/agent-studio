"""Tests for list_skills — paginated workspace skill enumeration."""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Module stubs ───────────────────────────────────────────────────────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _make_skills(n: int) -> list[dict]:
    return [
        {"skillId": f"sk-{i:03d}", "name": f"skill_{i:03d}",
         "description": f"desc {i}", "workspace_id": "ws-1"}
        for i in range(n)
    ]


def _patch_membership_and_table(monkeypatch, skills_items, role="viewer",
                                 last_evaluated_keys=None):
    """Set up workspace membership + skills DDB responses.

    skills_items can be a list of items (single page) or a list of pages
    (each a list of items). last_evaluated_keys controls pagination.
    """
    from tools import _scope

    workspaces = MagicMock()
    if role:
        workspaces.get_item.return_value = {"Item": {"role": role}}
    else:
        workspaces.get_item.return_value = {}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    # Pages config
    if skills_items and isinstance(skills_items[0], dict):
        # Single page
        pages = [skills_items]
    else:
        pages = skills_items

    keys = list(last_evaluated_keys or [None] * (len(pages) - 1) + [None])
    if len(keys) < len(pages):
        keys = keys + [None] * (len(pages) - len(keys))

    responses = []
    for i, page in enumerate(pages):
        resp = {"Items": page}
        if i < len(pages) - 1 and keys[i] is not None:
            resp["LastEvaluatedKey"] = keys[i]
        responses.append(resp)

    fake_table = MagicMock()
    fake_table.query.side_effect = responses

    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    return fake_resource, fake_table


def test_list_skills_denies_non_member(monkeypatch):
    """require_role rejects non-member callers."""
    from tools import list_skills as mod
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    out = json.loads(mod.list_skills())
    # Returns the empty envelope on permission denial
    assert out == {"items": [], "total": 0, "filtered": 0, "returned": 0}


def test_list_skills_happy_path(monkeypatch):
    """Returns a paginated, alphabetically sorted envelope."""
    from tools import list_skills as mod

    skills = _make_skills(5)
    fake_resource, _ = _patch_membership_and_table(monkeypatch, skills)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills())

    assert out["total"] == 5
    assert out["filtered"] == 5
    assert out["returned"] == 5
    assert len(out["items"]) == 5
    # Sorted alphabetically by name
    names = [item["name"] for item in out["items"]]
    assert names == sorted(names)


def test_list_skills_skips_deleted(monkeypatch):
    """deleted=True skills are excluded."""
    from tools import list_skills as mod

    items = [
        {"skillId": "alive", "name": "alive", "workspace_id": "ws-1"},
        {"skillId": "dead", "name": "dead", "workspace_id": "ws-1",
         "deleted": True},
    ]
    fake_resource, _ = _patch_membership_and_table(monkeypatch, items)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills())

    assert out["total"] == 1
    assert out["items"][0]["id"] == "alive"


def test_list_skills_name_pattern_filters_case_insensitive(monkeypatch):
    """name_pattern is a case-insensitive substring filter."""
    from tools import list_skills as mod

    items = [
        {"skillId": "1", "name": "DataAnalyzer", "workspace_id": "ws-1"},
        {"skillId": "2", "name": "PdfReader", "workspace_id": "ws-1"},
        {"skillId": "3", "name": "datacleaner", "workspace_id": "ws-1"},
    ]
    fake_resource, _ = _patch_membership_and_table(monkeypatch, items)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills(name_pattern="DATA"))

    assert out["total"] == 3
    assert out["filtered"] == 2
    names = [item["name"] for item in out["items"]]
    assert "PdfReader" not in names
    assert "DataAnalyzer" in names
    assert "datacleaner" in names


def test_list_skills_pagination_offset_and_limit(monkeypatch):
    """Limit and offset slice the sorted results."""
    from tools import list_skills as mod

    skills = _make_skills(10)
    fake_resource, _ = _patch_membership_and_table(monkeypatch, skills)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills(limit=3, offset=2))

    assert out["returned"] == 3
    assert out["total"] == 10
    assert len(out["items"]) == 3
    assert out["items"][0]["name"] == "skill_002"
    # hint should be present (more remain)
    assert "hint" in out


def test_list_skills_offset_past_end_returns_hint(monkeypatch):
    """When offset is beyond the last item, returns helpful hint."""
    from tools import list_skills as mod

    skills = _make_skills(3)
    fake_resource, _ = _patch_membership_and_table(monkeypatch, skills)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills(limit=5, offset=10))

    assert out["returned"] == 0
    assert "hint" in out
    assert "beyond the last item" in out["hint"]


def test_list_skills_clamps_invalid_limit(monkeypatch):
    """Invalid string limit defaults; negative/huge clamps."""
    from tools import list_skills as mod

    skills = _make_skills(3)
    fake_resource, _ = _patch_membership_and_table(monkeypatch, skills)

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        # Bad string limit → defaults to 50
        out = json.loads(mod.list_skills(limit="not-an-int", offset="bad"))

    # Should not error, returns up to 50
    assert out["total"] == 3
    assert out["returned"] == 3


def test_list_skills_paginates_ddb_results(monkeypatch):
    """When DDB returns LastEvaluatedKey, follows pagination cursor."""
    from tools import list_skills as mod
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": "viewer"}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    page1 = [{"skillId": "p1-a", "name": "p1_a", "workspace_id": "ws-1"}]
    page2 = [{"skillId": "p2-a", "name": "p2_a", "workspace_id": "ws-1"}]

    fake_table = MagicMock()
    fake_table.query.side_effect = [
        {"Items": page1, "LastEvaluatedKey": {"skillId": "p1-a"}},
        {"Items": page2},  # no LastEvaluatedKey → terminator
    ]
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills())

    assert out["total"] == 2
    assert fake_table.query.call_count == 2


def test_list_skills_handles_ddb_failure(monkeypatch):
    """DDB query exception returns {error: ...}."""
    from tools import list_skills as mod
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": "viewer"}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    fake_table = MagicMock()
    fake_table.query.side_effect = Exception("ddb explode")
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.resource") as mr:
        mr.return_value = fake_resource
        out = json.loads(mod.list_skills())

    assert "error" in out
    assert "ddb explode" in out["error"]
