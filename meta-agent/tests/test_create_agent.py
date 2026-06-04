"""Tests for create_agent — the zip-based agent creation tool.

Covers:
  1. RBAC enforcement (xfail — not currently implemented)
  2. Staging workspace_id override security gap (xfail)
  3. Happy path: staging_key → deploy → DDB write
  4. Partial failure cleanup (xfail — no orphan handling today)
  5. @tool detection regex gap (parametrized decorator form)
  6. Zip assembly: flat structure, overlay correctness
  7. Required fields validation
  8. MCP policy check integration
"""

import io
import json
import re
import sys
import types
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest


# ── Module stubs ───────────────────────────────────────────────────────────────

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
    "ACCOUNT_ID": "123456789012",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "TOOLS_TABLE": "agent-studio-tools",
    "AGENT_ROLE_ARN": "arn:aws:iam::123456789012:role/MetaAgent",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/agent-deployment.zip",
    "MODEL_ID": "global.anthropic.claude-opus-4-7",
    "PERMISSION_TIER_ROLES": {
        "basic": "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1",
        "readonly": "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1",
        "data-access": "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1",
    },
    "DEFAULT_PERMISSION_TIER": "readonly",
    "MCP_GATEWAY_URL": "",
    "CODE_INTERPRETER_ID": "",
    "BROWSER_ID": "",
}.items():
    setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config

# Mock tools_library.registry so the import inside create_agent resolves
_mock_registry = sys.modules.get("tools_library.registry") or types.ModuleType("tools_library.registry")
_mock_registry.get_tool_code_by_func_name = lambda name: None
_mock_registry._ALL_TOOLS = []
_mock_tl = sys.modules.get("tools_library") or types.ModuleType("tools_library")
_mock_tl.registry = _mock_registry
sys.modules["tools_library"] = _mock_tl
sys.modules["tools_library.registry"] = _mock_registry

# Real yaml is available via base/requirements.txt; do NOT stub sys.modules["yaml"]
# here — that pollutes other tests (e.g. import_skill.py uses yaml.safe_load to
# parse SKILL.md frontmatter, and would receive the stub's hard-coded value).


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    """Set Meta-Agent scope variables as main.py does at each invoke."""
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)
    monkeypatch.setattr(_scope, "_creator_language", "en", raising=False)


def _make_base_zip() -> bytes:
    """Create a minimal base deployment zip for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("requirements.txt", "strands-agents>=0.1\n")
        zf.writestr("some_lib.py", "# placeholder\n")
    return buf.getvalue()


def _make_staging(**overrides) -> dict:
    """Build a staging config dict with sensible defaults."""
    base = {
        "name": "testBot",
        "display_name": "Test Bot",
        "description": "A test agent",
        "system_prompt": "You are a helpful assistant.",
        "tool_definitions": '@tool\ndef greet(name: str = "") -> str:\n    """Say hello."""\n    return f"Hello {name}"',
        "tool_names": "greet",
        "welcome_message": "Hi there!",
        "suggestions": "Ask about X|Try Y|Help with Z",
        "workspace_id": "ws-test",
        "supports_images": False,
        "mcp_targets": "",
        "skills": [],
    }
    base.update(overrides)
    return base


def _staging_body(staged: dict) -> bytes:
    return json.dumps(staged).encode("utf-8")


def _mock_s3_get_staging(staged: dict):
    """Create a mock S3 client that returns the staging JSON."""
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: _staging_body(staged))
    }
    mock_s3.put_object.return_value = {}
    mock_s3.get_paginator.return_value.paginate.return_value = []
    return mock_s3


# ── 1. RBAC: viewer role should be rejected ────────────────────────────────────

class TestRBAC:
    @pytest.mark.xfail(
        reason="create_agent does not currently enforce RBAC — viewer callers can create agents. "
               "This documents the expected behavior that should be implemented.",
        strict=True,
    )
    def test_viewer_role_rejected(self, monkeypatch):
        """A caller with viewer role should NOT be able to create agents."""
        from tools import _scope
        monkeypatch.setattr(_scope, "_caller_id", "viewer-user", raising=False)
        monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)

        # Mock the workspace membership lookup to return viewer role
        mock_ws_table = MagicMock()
        mock_ws_table.get_item.return_value = {
            "Item": {"role": "viewer"}
        }

        from tools import create_agent as mod
        staged = _make_staging()

        with patch("boto3.client") as mock_boto_client, \
             patch("boto3.resource") as mock_boto_resource:
            mock_s3 = _mock_s3_get_staging(staged)
            mock_boto_client.return_value = mock_s3

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        # Expected: should reject with permission error
        assert "error" in result
        assert "permission" in result["error"].lower() or "viewer" in result["error"].lower()


# ── 2. Staging workspace_id override ──────────────────────────────────────────

class TestStagingWorkspaceOverride:
    def test_staging_workspace_id_used_for_role_lookup(self, monkeypatch):
        """When staging JSON has a workspace_id, it's used for _get_agent_role_arn.

        This documents the current behavior: the staging workspace_id
        overrides the scope workspace_id for IAM role selection.
        """
        from tools import create_agent as mod

        staged = _make_staging(workspace_id="ws-other")
        captured_ws_id = []

        def mock_get_agent_role_arn(ws_id):
            captured_ws_id.append(ws_id)
            return "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1"

        monkeypatch.setattr(mod, "_get_agent_role_arn", mock_get_agent_role_arn)
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"fake-zip")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "agents/testBot/deployment.zip")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-123",
            "agent_arn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/rt-123",
            "_s3_key": "agents/testBot/deployment.zip",
            "_role_arn": "arn:aws:iam::123456789012:role/AgentStudioSubAgent-basic-us-east-1",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mock_boto_client, \
             patch("boto3.resource") as mock_boto_resource:
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: _staging_body(staged))
            }
            mock_s3.put_object.return_value = {}
            mock_boto_client.return_value = mock_s3

            mock_ddb = MagicMock()
            mock_ddb.Table.return_value = MagicMock()
            mock_boto_resource.return_value = mock_ddb

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        # Current behavior: workspace_id from staging is used
        assert captured_ws_id == ["ws-other"]

    @pytest.mark.xfail(
        reason="Security gap: staging JSON workspace_id is trusted without verifying "
               "that the caller actually belongs to that workspace. A malicious caller "
               "could supply a different workspace_id to get a different IAM role.",
        strict=True,
    )
    def test_staging_workspace_id_cross_workspace_rejected(self, monkeypatch):
        """Should reject if staging workspace_id differs from caller's workspace."""
        from tools import create_agent as mod
        from tools import _scope

        # Caller's actual workspace
        monkeypatch.setattr(_scope, "_workspace_id", "ws-mine", raising=False)

        # Staging claims a DIFFERENT workspace
        staged = _make_staging(workspace_id="ws-someone-else")

        with patch("boto3.client") as mock_boto_client:
            mock_s3 = _mock_s3_get_staging(staged)
            mock_boto_client.return_value = mock_s3

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert "error" in result
        assert "workspace" in result["error"].lower()


# ── 3. Happy path ─────────────────────────────────────────────────────────────

class TestHappyPath:
    def test_staging_key_creates_agent_successfully(self, monkeypatch):
        """Full happy path: staging_key → download → validate → deploy → DDB."""
        from tools import create_agent as mod

        staged = _make_staging()

        monkeypatch.setattr(mod, "_get_agent_role_arn",
                            lambda ws: "arn:aws:iam::123:role/ws-role")

        # Track deploy calls
        deploy_calls = {}

        def mock_build_package(main_py, tools_py, prompt_txt, config_json):
            deploy_calls["build"] = {
                "main_py": main_py,
                "tools_py": tools_py,
                "prompt_txt": prompt_txt,
                "config_json": config_json,
            }
            return b"fake-zip-bytes"

        def mock_upload(name, package):
            deploy_calls["upload"] = {"name": name, "package_size": len(package)}
            return f"agents/{name}/deployment.zip"

        def mock_create_runtime(name, desc, s3_key, role_arn):
            deploy_calls["create_runtime"] = {
                "name": name, "desc": desc, "s3_key": s3_key, "role_arn": role_arn,
            }
            return {
                "agent_id": "rt-abc123",
                "agent_arn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/rt-abc123",
                "_s3_key": s3_key,
                "_role_arn": role_arn,
            }

        monkeypatch.setattr(mod, "build_deployment_package_v2", mock_build_package)
        monkeypatch.setattr(mod, "upload_deployment", mock_upload)
        monkeypatch.setattr(mod, "create_runtime", mock_create_runtime)
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        mock_ddb_table = MagicMock()
        mock_control = MagicMock()

        with patch("boto3.client") as mock_boto_client, \
             patch("boto3.resource") as mock_boto_resource:

            def client_factory(service, **kwargs):
                if service == "s3":
                    s3 = MagicMock()
                    s3.get_object.return_value = {
                        "Body": MagicMock(read=lambda: _staging_body(staged))
                    }
                    s3.put_object.return_value = {}
                    return s3
                if service == "bedrock-agentcore-control":
                    mock_control.update_agent_runtime.return_value = {}
                    return mock_control
                return MagicMock()

            mock_boto_client.side_effect = client_factory

            def resource_factory(service, **kwargs):
                r = MagicMock()
                r.Table.return_value = mock_ddb_table
                return r

            mock_boto_resource.side_effect = resource_factory

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/config.json",
            ))

        # Verify success
        assert result.get("agent_id") == "rt-abc123"
        assert result.get("status") == "READY"
        assert result.get("agent_name") == "testBot"

        # Verify deploy was called correctly
        assert deploy_calls["create_runtime"]["name"] == "testBot"
        assert deploy_calls["create_runtime"]["role_arn"] == "arn:aws:iam::123:role/ws-role"
        assert deploy_calls["upload"]["name"] == "testBot"

        # Verify DDB put_item was called
        mock_ddb_table.put_item.assert_called_once()
        item = mock_ddb_table.put_item.call_args.kwargs["Item"]
        assert item["agentId"] == "rt-abc123"
        assert item["agentName"] == "testBot"
        assert item["workspace_id"] == "ws-test"
        assert item["status"] == "active"
        assert item["welcome_message"] == "Hi there!"
        assert item["suggestions"] == ["Ask about X", "Try Y", "Help with Z"]

    def test_default_welcome_message_generated_when_empty(self, monkeypatch):
        """When welcome_message is empty, a default is generated."""
        from tools import create_agent as mod

        staged = _make_staging(welcome_message="", description="A data analyst")

        monkeypatch.setattr(mod, "_get_agent_role_arn",
                            lambda ws: "arn:aws:iam::123:role/ws-role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"zip")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "agents/x/d.zip")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()

            mock_table = MagicMock()
            mr.return_value.Table.return_value = mock_table

            mod.create_agent(agent_name="", staging_key="staging/test.json")

        item = mock_table.put_item.call_args.kwargs["Item"]
        # English default based on _creator_language = "en"
        assert "I'm testBot" in item["welcome_message"]
        assert "A data analyst" in item["welcome_message"]


# ── 4. Partial failure cleanup ─────────────────────────────────────────────────

class TestPartialFailureCleanup:
    @pytest.mark.xfail(
        reason="create_agent does not clean up orphaned runtimes when post-create "
               "steps (DDB write, wait_for_ready) fail. The runtime remains in "
               "AgentCore with no DDB record pointing to it.",
        strict=True,
    )
    def test_runtime_cleaned_up_on_ddb_failure(self, monkeypatch):
        """If DDB put_item fails after runtime creation, the runtime should be deleted."""
        from tools import create_agent as mod

        staged = _make_staging()

        monkeypatch.setattr(mod, "_get_agent_role_arn",
                            lambda ws: "arn:aws:iam::123:role/ws-role")
        monkeypatch.setattr("deploy.build_deployment_package_v2", lambda *a: b"zip")
        monkeypatch.setattr("deploy.upload_deployment", lambda *a: "agents/x/d.zip")
        monkeypatch.setattr("deploy.create_runtime", lambda *a, **kw: {
            "agent_id": "rt-orphan", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr("deploy.wait_for_ready", lambda *a: "READY")
        monkeypatch.setattr("deploy.validate_agent_files",
                            lambda *a: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr("deploy.build_skill_prompt_section", lambda *a: "")

        mock_control = MagicMock()
        mock_ddb_table = MagicMock()
        mock_ddb_table.put_item.side_effect = Exception("DDB write failed")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            def client_factory(svc, **kw):
                if svc == "s3":
                    s3 = MagicMock()
                    s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
                    s3.put_object.return_value = {}
                    return s3
                if svc == "bedrock-agentcore-control":
                    return mock_control
                return MagicMock()

            mc.side_effect = client_factory
            mr.return_value.Table.return_value = mock_ddb_table

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        # Expected: should have called delete_agent_runtime to clean up
        mock_control.delete_agent_runtime.assert_called_once_with(
            agentRuntimeId="rt-orphan"
        )
        assert "error" in result


# ── 5. @tool detection regex ───────────────────────────────────────────────────

class TestToolDetectionRegex:
    """Tests for the regex that finds @tool-decorated functions in custom code."""

    def test_simple_tool_decorator_detected(self):
        """Standard @tool\\ndef pattern is found."""
        code = '@tool\ndef my_func(x: str = "") -> str:\n    """Hi."""\n    return x'
        # This is the exact regex from create_agent.py line 347
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert matches == {"my_func"}

    def test_multiple_tools_detected(self):
        code = (
            '@tool\ndef alpha(x: str = "") -> str:\n    """A."""\n    return x\n\n'
            '@tool\ndef beta(y: int = 0) -> str:\n    """B."""\n    return str(y)'
        )
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert matches == {"alpha", "beta"}

    def test_tool_with_blank_line_before_def_detected(self):
        """@tool followed by newline then def is found."""
        code = '@tool\ndef spaced(x: str = "") -> str:\n    """S."""\n    return x'
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert matches == {"spaced"}

    @pytest.mark.xfail(
        reason="Known regex gap: @tool(...) parametrized decorator form is NOT detected. "
               "The regex `@tool\\s*\\ndef` requires nothing between @tool and the newline, "
               "so @tool(name='custom') or @tool(description='...') won't match.",
        strict=True,
    )
    def test_parametrized_tool_decorator_detected(self):
        """@tool(name='custom_name') should also be detected."""
        code = '@tool(name="custom")\ndef my_func(x: str = "") -> str:\n    """Hi."""\n    return x'
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert "my_func" in matches

    @pytest.mark.xfail(
        reason="Known regex gap: @tool with parentheses (e.g. @tool()) is NOT detected.",
        strict=True,
    )
    def test_empty_parens_tool_decorator_detected(self):
        """@tool() — empty-arg invocation — should also be detected."""
        code = '@tool()\ndef my_func(x: str = "") -> str:\n    """Hi."""\n    return x'
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert "my_func" in matches

    def test_non_tool_decorator_not_matched(self):
        """Other decorators should not be confused for @tool."""
        code = '@lru_cache\ndef cached() -> str:\n    return "cached"'
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert matches == set()

    def test_tool_in_comment_not_matched(self):
        """# @tool in a comment should not match."""
        code = '# @tool\n# def fake():\npass'
        matches = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
        assert matches == set()


# ── 6. Zip assembly ────────────────────────────────────────────────────────────

class TestZipAssembly:
    """Tests for build_deployment_package_v2 — flat structure, correct overlay."""

    def test_flat_structure_no_nested_dirs(self, monkeypatch):
        """Deployment zip must be flat (no site-packages/ nesting)."""
        from deploy import build_deployment_package_v2

        base_zip = _make_base_zip()

        with patch("boto3.client") as mc:
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: base_zip)
            }
            # NoSuchKey needs to be an exception class on the mock
            mock_s3.exceptions = MagicMock()
            mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
            mc.return_value = mock_s3

            result = build_deployment_package_v2(
                main_py="# main\n",
                tools_py="from strands import tool\n",
                prompt_txt="You are helpful.",
                config_json='{"model_id": "x", "tool_names": []}',
            )

        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            names = zf.namelist()
            # All agent files should be at root level
            assert "main.py" in names
            assert "tools.py" in names
            assert "prompt.txt" in names
            assert "config.json" in names
            # No nested directory structure for our files
            for name in ["main.py", "tools.py", "prompt.txt", "config.json"]:
                assert "/" not in name

    def test_agent_files_overlay_base(self, monkeypatch):
        """Agent-specific files replace base zip counterparts."""
        from deploy import build_deployment_package_v2

        # Base zip with a main.py that should be replaced
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("main.py", "# old main")
            zf.writestr("requirements.txt", "boto3\n")
            zf.writestr("helper.py", "# helper code")
        base_zip = buf.getvalue()

        with patch("boto3.client") as mc:
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: base_zip)
            }
            mock_s3.exceptions = MagicMock()
            mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
            mc.return_value = mock_s3

            result = build_deployment_package_v2(
                main_py="# NEW main\n",
                tools_py="from strands import tool\n",
                prompt_txt="New prompt.",
                config_json='{"model_id": "y", "tool_names": []}',
            )

        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            # main.py should be the NEW one, not old
            assert zf.read("main.py").decode() == "# NEW main\n"
            # Base files not in agent_files set should persist
            assert zf.read("requirements.txt").decode() == "boto3\n"
            assert zf.read("helper.py").decode() == "# helper code"

    def test_tools_directory_stripped_from_base(self, monkeypatch):
        """Base zip tools/ directory is removed to prevent import shadowing."""
        from deploy import build_deployment_package_v2

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("main.py", "# old")
            zf.writestr("tools/__init__.py", "# shadow")
            zf.writestr("tools/some_tool.py", "# tool code")
            zf.writestr("other.py", "# keep")
        base_zip = buf.getvalue()

        with patch("boto3.client") as mc:
            mock_s3 = MagicMock()
            mock_s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: base_zip)
            }
            mock_s3.exceptions = MagicMock()
            mock_s3.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
            mc.return_value = mock_s3

            result = build_deployment_package_v2(
                main_py="# main\n",
                tools_py="# tools\n",
                prompt_txt="p",
                config_json='{"model_id": "x", "tool_names": []}',
            )

        with zipfile.ZipFile(io.BytesIO(result), "r") as zf:
            names = zf.namelist()
            # tools/ directory entries must NOT be in output
            assert "tools/__init__.py" not in names
            assert "tools/some_tool.py" not in names
            assert "tools/" not in names
            # But other.py should remain
            assert "other.py" in names


# ── 7. Required fields validation ─────────────────────────────────────────────

class TestRequiredFieldsValidation:
    def test_validation_failure_returns_error(self, monkeypatch):
        """If validate_agent_files reports errors, create_agent stops early."""
        from tools import create_agent as mod

        staged = _make_staging(
            tool_definitions="def broken(:\n    pass",  # syntax error
        )

        monkeypatch.setattr(mod, "_get_agent_role_arn",
                            lambda ws: "arn:aws:iam::123:role/r")
        monkeypatch.setattr(mod, "validate_agent_files", lambda *a, **kw: {
            "valid": False,
            "errors": ["tools.py SyntaxError: invalid syntax (line 1)"],
            "warnings": [],
        })
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            mc.return_value = s3
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert "error" in result
        assert "validation" in result["error"].lower() or "SyntaxError" in str(result.get("details", ""))

    def test_staging_read_failure_returns_error(self, monkeypatch):
        """If S3 staging read fails, return a clean error."""
        from tools import create_agent as mod

        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("NoSuchKey: staging/missing.json")
            mc.return_value = s3

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/missing.json",
            ))

        assert "error" in result
        assert "staging" in result["error"].lower()


# ── 8. MCP policy check integration ───────────────────────────────────────────

class TestMCPPolicyCheck:
    def test_check_mcp_policy_mode_all_allows_everything(self):
        from tools.create_agent import _check_mcp_policy

        policy = {"mode": "all"}
        denied = _check_mcp_policy(["cloudwatch", "iam", "custom"], policy)
        assert denied == []

    def test_check_mcp_policy_allowlist_blocks_unlisted(self):
        from tools.create_agent import _check_mcp_policy

        policy = {"mode": "allowlist", "allowedTargets": ["cloudwatch", "s3"]}
        denied = _check_mcp_policy(["cloudwatch", "iam", "s3"], policy)
        assert denied == ["iam"]

    def test_check_mcp_policy_denylist_blocks_listed(self):
        from tools.create_agent import _check_mcp_policy

        policy = {"mode": "denylist", "deniedTargets": ["dangerous-tool"]}
        denied = _check_mcp_policy(["safe-tool", "dangerous-tool"], policy)
        assert denied == ["dangerous-tool"]

    def test_check_mcp_policy_unknown_mode_allows_all(self):
        from tools.create_agent import _check_mcp_policy

        policy = {"mode": "unknown-future-mode"}
        denied = _check_mcp_policy(["anything"], policy)
        assert denied == []

    def test_denied_mcp_targets_abort_creation(self, monkeypatch):
        """If MCP targets are denied by workspace policy, create_agent fails early."""
        from tools import create_agent as mod

        staged = _make_staging(mcp_targets="blocked-target")

        monkeypatch.setattr(
            mod, "_get_workspace_mcp_policy",
            lambda ws: {"mode": "allowlist", "allowedTargets": ["allowed-only"]},
        )

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            mc.return_value = s3
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert "error" in result
        assert "blocked-target" in result["error"]
        assert "not allowed" in result["error"].lower()

    def test_get_workspace_mcp_policy_reads_from_ddb(self, monkeypatch):
        """_get_workspace_mcp_policy fetches from workspaces table."""
        from tools.create_agent import _get_workspace_mcp_policy

        with patch("boto3.resource") as mr:
            mock_table = MagicMock()
            mock_table.get_item.return_value = {
                "Item": {
                    "workspaceId": "ws-1",
                    "sk": "META",
                    "mcpPolicy": {"mode": "denylist", "deniedTargets": ["x"]},
                }
            }
            mr.return_value.Table.return_value = mock_table

            policy = _get_workspace_mcp_policy("ws-1")

        assert policy == {"mode": "denylist", "deniedTargets": ["x"]}

    def test_get_workspace_mcp_policy_defaults_to_all(self, monkeypatch):
        """Missing mcpPolicy in workspace metadata defaults to mode=all."""
        from tools.create_agent import _get_workspace_mcp_policy

        with patch("boto3.resource") as mr:
            mock_table = MagicMock()
            mock_table.get_item.return_value = {"Item": {"workspaceId": "ws-1"}}
            mr.return_value.Table.return_value = mock_table

            policy = _get_workspace_mcp_policy("ws-1")

        assert policy == {"mode": "all"}

    def test_get_workspace_mcp_policy_empty_workspace_returns_all(self):
        """Empty workspace_id returns default all policy (no DDB call)."""
        from tools.create_agent import _get_workspace_mcp_policy

        policy = _get_workspace_mcp_policy("")
        assert policy == {"mode": "all"}


# ── Additional edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_suggestions_list_in_staging_joined(self, monkeypatch):
        """Staging JSON with suggestions as list is joined with |."""
        from tools import create_agent as mod

        staged = _make_staging(suggestions=["One", "Two", "Three"])

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.return_value = MagicMock()
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()

            mock_table = MagicMock()
            mr.return_value.Table.return_value = mock_table

            mod.create_agent(agent_name="", staging_key="staging/test.json")

        item = mock_table.put_item.call_args.kwargs["Item"]
        assert item["suggestions"] == ["One", "Two", "Three"]

    def test_mcp_targets_list_in_staging_joined(self, monkeypatch):
        """Staging JSON with mcp_targets as list is joined with comma."""
        from tools import create_agent as mod

        staged = _make_staging(mcp_targets=["cloudwatch", "s3"])

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_resolve_mcp_endpoints", lambda targets: [
            {"type": "runtime", "name": t, "target_name": t, "auth": "runtime"}
            for t in targets
        ])
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.return_value = MagicMock()
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()

            mock_table = MagicMock()
            mr.return_value.Table.return_value = mock_table

            mod.create_agent(agent_name="", staging_key="staging/test.json")

        item = mock_table.put_item.call_args.kwargs["Item"]
        assert item["mcp_targets"] == ["cloudwatch", "s3"]

    def test_builtin_tool_injected_when_not_in_definitions(self, monkeypatch):
        """Tools in tool_names but missing from tool_definitions get injected from registry."""
        from tools import create_agent as mod

        staged = _make_staging(
            tool_definitions="",
            tool_names="web_search",
        )

        # Mock the builtin tool registry to return code for web_search
        monkeypatch.setattr(
            mod, "_get_builtin_code",
            lambda name: '@tool\ndef web_search(query: str = "") -> str:\n    """Search the web."""\n    return ""' if name == "web_search" else None,
        )
        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        captured_tools_py = []

        def mock_validate(main_py, tools_py, prompt_txt, config_json):
            captured_tools_py.append(tools_py)
            return {"valid": True, "errors": [], "warnings": []}

        monkeypatch.setattr(mod, "validate_agent_files", mock_validate)
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            mod.create_agent(agent_name="", staging_key="staging/test.json")

        # The tools.py passed to validate should contain web_search from registry
        assert len(captured_tools_py) == 1
        assert "web_search" in captured_tools_py[0]


# ── 9. _resolve_mcp_endpoints helper ─────────────────────────────────────────

class TestResolveMcpEndpoints:
    def test_remote_target_from_registry(self, monkeypatch):
        """A target listed in the registry's remote_targets becomes a remote endpoint."""
        from tools import create_agent as mod

        # Mock yaml registry response via S3.get_object
        registry_yaml = (
            "remote_targets:\n"
            "  - name: aws-knowledge\n"
            "    enabled: true\n"
            "    endpoint: https://knowledge.example.com\n"
            "    auth: none\n"
        )
        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: registry_yaml.encode())}
            mc.return_value = s3
            endpoints = mod._resolve_mcp_endpoints(["aws-knowledge"])

        assert len(endpoints) == 1
        assert endpoints[0]["type"] == "remote"
        assert endpoints[0]["url"] == "https://knowledge.example.com"
        assert endpoints[0]["auth"] == "none"

    def test_remote_target_with_auth_aws_mcp(self, monkeypatch):
        """Non-'none' auth is normalized to aws-mcp."""
        from tools import create_agent as mod

        registry_yaml = (
            "remote_targets:\n"
            "  - name: pricing\n"
            "    enabled: true\n"
            "    endpoint: https://pricing.example.com\n"
            "    auth: sigv4\n"
        )
        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: registry_yaml.encode())}
            mc.return_value = s3
            endpoints = mod._resolve_mcp_endpoints(["pricing"])

        assert endpoints[0]["auth"] == "aws-mcp"

    def test_disabled_remote_target_not_in_map(self, monkeypatch):
        """enabled: false targets are skipped from the remote map."""
        from tools import create_agent as mod

        registry_yaml = (
            "remote_targets:\n"
            "  - name: legacy\n"
            "    enabled: false\n"
            "    endpoint: https://legacy.example.com\n"
            "    auth: none\n"
        )
        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: registry_yaml.encode())}
            # No bedrock-agentcore-control needed because runtime targets list is empty
            def factory(svc, **kw):
                if svc == "s3":
                    return s3
                ctrl = MagicMock()
                ctrl.list_agent_runtimes.return_value = {"agentRuntimes": []}
                return ctrl
            mc.side_effect = factory
            endpoints = mod._resolve_mcp_endpoints(["legacy"])

        # Falls through to runtime-target branch (since "legacy" is not in remote_map)
        assert endpoints[0]["type"] == "runtime"

    def test_availability_filters_region(self, monkeypatch):
        """A target whose availability list excludes REGION is skipped."""
        from tools import create_agent as mod

        registry_yaml = (
            "remote_targets:\n"
            "  - name: only-west\n"
            "    enabled: true\n"
            "    endpoint: https://only-west.example.com\n"
            "    auth: none\n"
            "    availability: [us-west-2]\n"
        )
        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: registry_yaml.encode())}
            def factory(svc, **kw):
                if svc == "s3":
                    return s3
                ctrl = MagicMock()
                ctrl.list_agent_runtimes.return_value = {"agentRuntimes": []}
                return ctrl
            mc.side_effect = factory
            endpoints = mod._resolve_mcp_endpoints(["only-west"])

        # Region is us-east-1 from mock_config, so only-west is filtered out
        assert endpoints[0]["type"] == "runtime"

    def test_yaml_load_failure_uses_fallback(self, monkeypatch):
        """When S3 fetch fails, fallback hardcoded remotes are used."""
        from tools import create_agent as mod

        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("registry missing")
            def factory(svc, **kw):
                if svc == "s3":
                    return s3
                ctrl = MagicMock()
                ctrl.list_agent_runtimes.return_value = {"agentRuntimes": []}
                return ctrl
            mc.side_effect = factory
            endpoints = mod._resolve_mcp_endpoints(["aws-knowledge"])

        assert endpoints[0]["type"] == "remote"
        assert "aws-knowledge" == endpoints[0]["name"]

    def test_runtime_target_resolves_to_runtime_endpoint(self, monkeypatch):
        """A target not in remote_map becomes a runtime endpoint."""
        from tools import create_agent as mod

        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("no registry")
            ctrl = MagicMock()
            ctrl.list_agent_runtimes.return_value = {
                "agentRuntimes": [{"agentRuntimeName": "my_runtime"}]
            }
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else ctrl

            endpoints = mod._resolve_mcp_endpoints(["my-runtime"])

        assert endpoints[0]["type"] == "runtime"
        assert endpoints[0]["target_name"] == "my_runtime"
        assert endpoints[0]["auth"] == "runtime"

    def test_runtime_target_missing_emits_warning(self, monkeypatch, capsys):
        """A runtime target not deployed prints a stderr warning."""
        from tools import create_agent as mod

        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("no registry")
            ctrl = MagicMock()
            ctrl.list_agent_runtimes.return_value = {"agentRuntimes": []}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else ctrl

            mod._resolve_mcp_endpoints(["unknown-rt"])

        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "unknown_rt" in captured.err

    def test_paginated_runtime_listing(self, monkeypatch):
        """list_agent_runtimes pagination is followed via nextToken."""
        from tools import create_agent as mod

        with patch("boto3.client") as mc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("no registry")
            ctrl = MagicMock()
            ctrl.list_agent_runtimes.side_effect = [
                {"agentRuntimes": [{"agentRuntimeName": "first"}], "nextToken": "tok-1"},
                {"agentRuntimes": [{"agentRuntimeName": "page2"}]},  # No nextToken — terminates
            ]
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else ctrl

            endpoints = mod._resolve_mcp_endpoints(["page2"])

        assert endpoints[0]["target_name"] == "page2"
        # both pages were consulted
        assert ctrl.list_agent_runtimes.call_count == 2


# ── 10. Library-skill conversational path (skill_names) ───────────────────────

class TestSkillNamesPath:
    def test_skill_names_resolution_failure_aborts(self, monkeypatch):
        """If skill_names contains an unresolvable name, return error."""
        from tools import create_agent as mod

        # Stub the resolver imports inside the function so we can drive results.
        import tools.sync_agent_skill as sas

        def _fake_resolve(name, explicit):
            return None, "not_found"
        monkeypatch.setattr(sas, "_resolve_library_skill", _fake_resolve)
        monkeypatch.setattr(sas, "_read_library_skill_files", lambda s3, sid: {})
        monkeypatch.setattr(sas, "_compute_content_hash", lambda f: "abc12345")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            mc.return_value = s3
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="MyBot",
                description="d",
                system_prompt="hello",
                skill_names="ghost-skill",
            ))

        assert "error" in result
        assert "ghost-skill" in str(result.get("unresolved", []))

    def test_skill_names_no_skill_md_aborts(self, monkeypatch):
        """If a library skill resolves but has no SKILL.md, fail with hint."""
        from tools import create_agent as mod
        import tools.sync_agent_skill as sas

        monkeypatch.setattr(sas, "_resolve_library_skill",
                            lambda name, expl: ({"skillId": "s-1", "name": name, "description": "d"}, None))
        monkeypatch.setattr(sas, "_read_library_skill_files",
                            lambda s3, sid: {"other.txt": "x"})  # no SKILL.md
        monkeypatch.setattr(sas, "_compute_content_hash", lambda f: "abc12345")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            mc.return_value = s3
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="MyBot",
                description="d",
                system_prompt="hello",
                skill_names="empty-skill",
            ))

        assert "error" in result
        assert "no SKILL.md" in str(result.get("unresolved", []))

    def test_skill_names_happy_path_copies_library_files(self, monkeypatch):
        """skill_names path resolves, deploys, and copies library files post-create."""
        from tools import create_agent as mod
        import tools.sync_agent_skill as sas

        monkeypatch.setattr(sas, "_resolve_library_skill",
                            lambda name, expl: ({"skillId": "lib-skill-1",
                                                  "name": name, "description": "good"}, None))
        monkeypatch.setattr(sas, "_read_library_skill_files",
                            lambda s3, sid: {"SKILL.md": "# Body", "scripts/foo.py": "print(1)"})
        monkeypatch.setattr(sas, "_compute_content_hash", lambda f: "deadbeef")

        copy_calls = []

        def fake_copy_prefix(s3, src, dst):
            copy_calls.append({"src": src, "dst": dst})
            return 2

        monkeypatch.setattr(sas, "_copy_prefix", fake_copy_prefix)

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:aws:iam::123:role/r")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"zip")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-skill", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "\n## Skills\n")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="SkillyBot",
                description="d",
                system_prompt="hello",
                skill_names="great-skill",
            ))

        assert result.get("agent_id") == "rt-skill"
        # _copy_prefix called once with library prefix → agent prefix
        assert len(copy_calls) == 1
        assert copy_calls[0]["src"] == "skills/lib-skill-1/"
        assert copy_calls[0]["dst"].startswith("agents/rt-skill/skills/")

    def test_skill_names_copy_failure_logs_warning(self, monkeypatch, capsys):
        """If _copy_prefix raises, the create still succeeds but a warning is logged."""
        from tools import create_agent as mod
        import tools.sync_agent_skill as sas

        monkeypatch.setattr(sas, "_resolve_library_skill",
                            lambda name, expl: ({"skillId": "lib-1",
                                                  "name": name, "description": "d"}, None))
        monkeypatch.setattr(sas, "_read_library_skill_files",
                            lambda s3, sid: {"SKILL.md": "# Body"})
        monkeypatch.setattr(sas, "_compute_content_hash", lambda f: "abc")

        def boom(s3, src, dst):
            raise Exception("copy denied")
        monkeypatch.setattr(sas, "_copy_prefix", boom)

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="WarnBot",
                description="d",
                system_prompt="hello",
                skill_names="boom-skill",
            ))

        assert result.get("agent_id") == "rt-1"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "boom-skill" not in captured.err  # We log library_id, not name


# ── 11. Staging skills with SKILL.md fetch from S3 ────────────────────────────

class TestStagingSkillFlow:
    def test_staging_skill_md_read_from_s3(self, monkeypatch):
        """Staging-driven skill triggers SKILL.md S3 fetch + post-create file copy."""
        from tools import create_agent as mod

        staged = _make_staging(
            agent_id="draft-id-xyz",
            skills=[{
                "id": "skill-abc",
                "name": "DataSkill",
                "description": "Draft skill",
                "contentHash": "h1",
            }],
        )

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-final", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section",
                            lambda data: "\n## Skills section ##\n" if data else "")

        s3_calls = {"copy": [], "list": []}

        def fake_paginator(method):
            assert method == "list_objects_v2"
            class Paginator:
                def paginate(self, **kw):
                    s3_calls["list"].append(kw)
                    return iter([{
                        "Contents": [
                            {"Key": f"agents/draft-id-xyz/skills/skill-abc/SKILL.md"},
                            {"Key": f"agents/draft-id-xyz/skills/skill-abc/scripts/foo.py"},
                            {"Key": f"agents/draft-id-xyz/skills/skill-abc/"},  # empty rel
                        ]
                    }])
            return Paginator()

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            def get_object(Bucket, Key, **kw):
                if Key == "staging/test.json":
                    return {"Body": MagicMock(read=lambda: _staging_body(staged))}
                if Key.endswith("SKILL.md"):
                    return {"Body": MagicMock(read=lambda: b"# Skill Body")}
                return {"Body": MagicMock(read=lambda: b"x")}
            s3.get_object.side_effect = get_object
            s3.put_object.return_value = {}
            s3.copy_object.side_effect = lambda **kw: s3_calls["copy"].append(kw)
            s3.get_paginator = fake_paginator

            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert result.get("agent_id") == "rt-final"
        # Two non-empty rels were copied (SKILL.md and scripts/foo.py); empty rel skipped
        assert len(s3_calls["copy"]) == 2
        # All copies go from draft to rt-final
        for c in s3_calls["copy"]:
            assert "draft-id-xyz" in c["CopySource"]["Key"]
            assert c["Key"].startswith("agents/rt-final/skills/skill-abc/")

    def test_staging_skill_md_read_failure_warns(self, monkeypatch, capsys):
        """A failure reading SKILL.md doesn't abort the create — it just warns."""
        from tools import create_agent as mod

        staged = _make_staging(
            skills=[{"id": "broken", "name": "BadSkill", "description": "d"}],
        )

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()

            def get_object(Bucket, Key, **kw):
                if Key == "staging/test.json":
                    return {"Body": MagicMock(read=lambda: _staging_body(staged))}
                if Key.endswith("SKILL.md"):
                    raise Exception("AccessDenied")
                return {"Body": MagicMock(read=lambda: b"x")}
            s3.get_object.side_effect = get_object
            s3.put_object.return_value = {}
            s3.get_paginator.return_value.paginate.return_value = iter([])

            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert result.get("agent_id") == "rt-1"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "broken" in captured.err

    def test_staging_skill_copy_failure_warns(self, monkeypatch, capsys):
        """If skill file copy raises, we still succeed and warn."""
        from tools import create_agent as mod

        staged = _make_staging(
            agent_id="draft-id",
            skills=[{"id": "s1", "name": "n", "description": "d", "contentHash": "h"}],
        )

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-final", "agent_arn": "arn:x",
            "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()

            def get_object(Bucket, Key, **kw):
                if Key == "staging/test.json":
                    return {"Body": MagicMock(read=lambda: _staging_body(staged))}
                if Key.endswith("SKILL.md"):
                    return {"Body": MagicMock(read=lambda: b"# md")}
                return {"Body": MagicMock(read=lambda: b"")}
            s3.get_object.side_effect = get_object
            s3.put_object.return_value = {}

            def boom_paginator(method):
                class P:
                    def paginate(self, **kw):
                        raise Exception("S3 list permission denied")
                return P()
            s3.get_paginator = boom_paginator

            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(
                agent_name="", staging_key="staging/test.json",
            ))

        assert result.get("agent_id") == "rt-final"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "s1" in captured.err

    def test_default_welcome_chinese_when_zh_locale(self, monkeypatch):
        """Empty welcome with zh creator language gets the Chinese fallback."""
        from tools import create_agent as mod
        from tools import _scope

        monkeypatch.setattr(_scope, "_creator_language", "zh-CN", raising=False)

        staged = _make_staging(welcome_message="", description="一个数据分析师")

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:role")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x", "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mock_table = MagicMock()
            mr.return_value.Table.return_value = mock_table

            mod.create_agent(agent_name="", staging_key="staging/x.json")

        item = mock_table.put_item.call_args.kwargs["Item"]
        assert "我是 testBot" in item["welcome_message"]


# ── 12. Post-create env-var update warning path ───────────────────────────────

class TestPostCreateEnvUpdate:
    def test_post_create_env_update_failure_warns(self, monkeypatch, capsys):
        """If update_agent_runtime fails after wait_for_ready, log warning, return success."""
        from tools import create_agent as mod

        staged = _make_staging()

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x", "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            ctrl = MagicMock()
            ctrl.update_agent_runtime.side_effect = Exception("control plane down")

            def factory(svc, **kw):
                if svc == "s3":
                    return s3
                if svc == "bedrock-agentcore-control":
                    return ctrl
                return MagicMock()
            mc.side_effect = factory
            mr.return_value.Table.return_value = MagicMock()

            result = json.loads(mod.create_agent(agent_name="", staging_key="staging/x.json"))

        # Failure of post-create env update is non-fatal
        assert result.get("agent_id") == "rt-1"
        assert result.get("status") == "READY"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "post-create env update failed" in captured.err


# ── 13. KB injection branch (workspace_id + kb_ids) ───────────────────────────

class TestKbInjection:
    def test_kb_inject_appends_kb_retrieve(self, monkeypatch):
        """When staging has knowledge_bases + workspace_id, kb_retrieve is added."""
        from tools import create_agent as mod

        staged = _make_staging(knowledge_bases=["kb-1"])

        # Inject a fake kb_inject module to bypass real bedrock-agent calls.
        # Use monkeypatch.setitem so the injection is reverted after the test —
        # otherwise it pollutes test_kb_tools.py which imports the real
        # tools.kb_inject and asserts its behavior.
        kb_mod = types.ModuleType("tools.kb_inject")
        kb_mod.resolve_kb_bindings = lambda ws, ids: [{"id": "kb-1", "name": "X"}]
        kb_mod.build_kb_injection = lambda recs: '@tool\ndef kb_retrieve(q: str = "") -> str:\n    return ""'
        monkeypatch.setitem(sys.modules, "tools.kb_inject", kb_mod)

        captured = []

        def mock_validate(main_py, tools_py, prompt_txt, config_json):
            captured.append({"tools_py": tools_py, "config": json.loads(config_json)})
            return {"valid": True, "errors": [], "warnings": []}

        monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn")
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x", "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files", mock_validate)
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": MagicMock(read=lambda: _staging_body(staged))}
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mr.return_value.Table.return_value = MagicMock()

            mod.create_agent(agent_name="", staging_key="staging/test.json")

        # kb_retrieve was added to config tool_names
        assert "kb_retrieve" in captured[0]["config"]["tool_names"]
        # kb code was appended to tools.py
        assert "kb_retrieve" in captured[0]["tools_py"]


# ── 14. Workspace fallback when no staging ────────────────────────────────────

class TestNoStagingPath:
    def test_no_staging_uses_scope_workspace(self, monkeypatch):
        """When called without staging_key, workspace_id falls back to module-level _workspace_id."""
        from tools import create_agent as mod

        # The fallback path reads `_workspace_id` attribute from tools.create_agent
        # itself (not tools._scope). Set it so the fallback can pick it up.
        monkeypatch.setattr(mod, "_workspace_id", "ws-test", raising=False)

        captured = {}

        def role_arn(ws):
            captured["ws"] = ws
            return "arn:role"

        monkeypatch.setattr(mod, "_get_agent_role_arn", role_arn)
        monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"z")
        monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "k")
        monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {
            "agent_id": "rt-1", "agent_arn": "arn:x", "_s3_key": "k", "_role_arn": "r",
        })
        monkeypatch.setattr(mod, "wait_for_ready", lambda *a, **kw: "READY")
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr(mod, "build_skill_prompt_section", lambda *a, **kw: "")

        with patch("boto3.client") as mc, patch("boto3.resource") as mr:
            s3 = MagicMock()
            s3.put_object.return_value = {}
            mc.side_effect = lambda svc, **kw: s3 if svc == "s3" else MagicMock()
            mock_table = MagicMock()
            mr.return_value.Table.return_value = mock_table

            mod.create_agent(
                agent_name="DirectBot",
                description="d",
                system_prompt="hello",
            )

        # _scope set _workspace_id="ws-test" — fallback should pick that up
        assert captured["ws"] == "ws-test"
        item = mock_table.put_item.call_args.kwargs["Item"]
        assert item["workspace_id"] == "ws-test"
