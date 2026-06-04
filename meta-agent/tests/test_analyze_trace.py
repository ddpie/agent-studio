"""Tests for analyze_trace tool.

analyze_trace is a small synthesizer with no AWS calls — it just shapes
the four input strings into a SKILL suggestion JSON. Tests guard the
output schema so downstream UI code (which keys off `suggested_skill.*`
and `action_needed`) isn't broken by a refactor.
"""

import json
import sys
import types
from unittest.mock import MagicMock

# ── Module stubs so `from strands import tool` works in-process ───────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands


def test_analyze_trace_returns_schema():
    from tools.analyze_trace import analyze_trace

    out = json.loads(
        analyze_trace(
            agent_name="dataBot",
            task_description="Summarize sales report",
            tools_used="s3_read,chart",
            steps_taken="1) read csv; 2) chart it",
        )
    )

    assert "suggested_skill" in out
    skill = out["suggested_skill"]
    assert skill["name"] == "dataBot-workflow"
    assert "Summarize sales report" in skill["description"]
    assert skill["type"] == "prompt"
    assert "1) read csv" in skill["instructions"]
    assert skill["tools_required"] == "s3_read,chart"

    assert "recommendation" in out
    assert "Skill" in out["recommendation"]
    assert out["action_needed"] == "User confirmation to save as Skill"


def test_analyze_trace_returns_valid_json():
    """Tool returns a JSON string — round-trips cleanly."""
    from tools.analyze_trace import analyze_trace

    raw = analyze_trace("a", "b", "c", "d")
    assert isinstance(raw, str)
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_analyze_trace_handles_empty_strings():
    """No exceptions when inputs are empty — important for defensive use."""
    from tools.analyze_trace import analyze_trace

    out = json.loads(analyze_trace("", "", "", ""))
    # Still well-formed
    assert out["suggested_skill"]["name"] == "-workflow"
    assert out["suggested_skill"]["instructions"] == ""
    assert out["suggested_skill"]["tools_required"] == ""


def test_analyze_trace_preserves_unicode():
    """Names/instructions in non-ASCII pass through unchanged."""
    from tools.analyze_trace import analyze_trace

    out = json.loads(
        analyze_trace(
            agent_name="财务机器人",
            task_description="生成季度报表",
            tools_used="excel_read",
            steps_taken="读取数据后导出",
        )
    )
    assert out["suggested_skill"]["name"] == "财务机器人-workflow"
    assert "生成季度报表" in out["suggested_skill"]["description"]
    assert out["suggested_skill"]["instructions"] == "读取数据后导出"
