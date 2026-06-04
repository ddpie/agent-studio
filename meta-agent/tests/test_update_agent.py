"""Tests for update_agent — in-place re-deploy of an existing agent.

Covers:
  1. Helper: _clean_tool_definitions
  2. Default welcome (en/zh)
  3. Staging-key parameter merge (success + failure)
  4. MCP policy enforcement (denied targets abort)
  5. Role gating: editor required
  6. Field updates: description, prompt, tools, mcp_targets, skills,
     suggestions, welcome_message, supports_images, display_name
  7. Skill SKILL.md fetch + per-skill warning when fetch fails
  8. Builtin tool injection when tool_names lists a builtin not in defs
  9. KB injection (knowledge_bases in staging or DDB)
 10. Existing-config preservation when fields are blank
 11. Validation failure path
 12. Workspace fallback (no staging) — uses module-level _workspace_id
"""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Module stubs (mirror test_create_agent.py) ────────────────────────────────

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
    },
    "DEFAULT_PERMISSION_TIER": "readonly",
    "MCP_GATEWAY_URL": "",
    "CODE_INTERPRETER_ID": "",
    "BROWSER_ID": "",
    "KB_TABLE": "agent-studio-knowledge-bases",
    "KB_SERVICE_ROLE_ARN": "",
    "VECTORS_BUCKET": "",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config

# tools_library.registry: provide both attributes used by create_agent and update_agent
_mock_registry = sys.modules.get("tools_library.registry") or types.ModuleType("tools_library.registry")
if not hasattr(_mock_registry, "get_tool_code_by_func_name"):
    _mock_registry.get_tool_code_by_func_name = lambda name: None
if not hasattr(_mock_registry, "_ALL_TOOLS"):
    _mock_registry._ALL_TOOLS = []
_mock_tl = sys.modules.get("tools_library") or types.ModuleType("tools_library")
_mock_tl.registry = _mock_registry
sys.modules["tools_library"] = _mock_tl
sys.modules["tools_library.registry"] = _mock_registry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    """Set Meta-Agent scope variables as main.py does at each invoke."""
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)
    monkeypatch.setattr(_scope, "_creator_language", "en", raising=False)


def _existing_metadata(**overrides):
    base = {
        "agent_id": "rt-existing",
        "name": "MyAgent",
        "display_name": "MyAgent",
        "description": "old description",
        "model_id": "old-model",
        "system_prompt": "OLD PROMPT",
        "tool_definitions": '@tool\ndef hello() -> str:\n    """Hi."""\n    return ""',
        "tools": ["hello"],
        "welcome_message": "old welcome",
        "suggestions": ["old1", "old2"],
        "template_id": "",
        "mcp_targets": [],
        "skills": [],
        "supports_images": False,
        "extra_env_vars": {"FOO": "bar"},
        "linked_agents": ["rt-other"],
        "deployedSkillHashes": {},
        "created_at": "2026-01-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _patch_deploy_chain(monkeypatch, mod):
    """Patch all heavy deploy helpers so the call returns synchronously."""
    monkeypatch.setattr(mod, "build_deployment_package_v2", lambda *a, **kw: b"fake-zip")
    monkeypatch.setattr(mod, "upload_deployment", lambda *a, **kw: "agents/rt/deploy.zip")
    monkeypatch.setattr(mod, "validate_agent_files",
                        lambda *a, **kw: {"valid": True, "errors": []})
    monkeypatch.setattr(mod, "build_skill_prompt_section",
                        lambda data: "\n## Skills\n" if data else "")
    monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:aws:iam::000:role/r")
    monkeypatch.setattr(mod, "get_base_guidelines", lambda lang: "")


def _make_s3(meta_dict, staging_dict=None, *, fail_skill_md=False):
    """Build a mock S3 client that returns metadata + optional staging JSON.

    Args:
        meta_dict: dict written under agents/<agent_id>/metadata.json
        staging_dict: dict written under staging/<key>
        fail_skill_md: when True, get_object on SKILL.md raises Exception
    """
    s3 = MagicMock()

    def get_object(Bucket=None, Key=None, **kw):
        if Key and Key.startswith("agents/") and Key.endswith("/metadata.json"):
            return {"Body": MagicMock(read=lambda: json.dumps(meta_dict).encode())}
        if Key and Key.startswith("agents/") and Key.endswith("/config.json"):
            raise Exception("NoSuchKey")
        if Key and Key.startswith("staging/") and staging_dict is not None:
            return {"Body": MagicMock(read=lambda: json.dumps(staging_dict).encode())}
        if Key and Key.endswith("SKILL.md"):
            if fail_skill_md:
                raise Exception("AccessDenied")
            return {"Body": MagicMock(read=lambda: b"# Skill body")}
        raise Exception(f"unexpected key: {Key}")

    s3.get_object.side_effect = get_object
    s3.put_object.return_value = {}
    return s3


def _patch_boto(meta_dict, staging_dict=None, *, fail_skill_md=False, control=None,
                fake_table=None, dynamodb_get_item=None):
    """Build a context manager that patches boto3.client + boto3.resource for update_agent."""
    s3 = _make_s3(meta_dict, staging_dict, fail_skill_md=fail_skill_md)
    ctrl = control if control is not None else MagicMock()
    ddb_low = MagicMock()
    # Default to empty Item so the KB-injection path stays inactive unless
    # the test explicitly opts in via dynamodb_get_item.
    ddb_low.get_item.return_value = (
        dynamodb_get_item if dynamodb_get_item is not None else {"Item": {}}
    )

    def client_factory(service, **kw):
        if service == "s3":
            return s3
        if service == "bedrock-agentcore-control":
            return ctrl
        if service == "dynamodb":
            return ddb_low
        return MagicMock()

    table = fake_table if fake_table is not None else MagicMock()
    res_mock = MagicMock()
    res_mock.Table.return_value = table

    return s3, ctrl, table, ddb_low, client_factory, res_mock


# ── 1. _clean_tool_definitions helper ─────────────────────────────────────────

class TestCleanToolDefinitions:
    def test_empty_input_returns_empty(self):
        from tools.update_agent import _clean_tool_definitions
        assert _clean_tool_definitions("") == ""

    def test_no_tool_decorator_returns_unchanged(self):
        from tools.update_agent import _clean_tool_definitions
        defs = "def foo():\n    pass"
        assert _clean_tool_definitions(defs) == defs

    def test_strips_internal_helpers_outside_tool(self):
        """Lines like async def _x() / @app. / if __name__ outside @tool blocks are dropped."""
        from tools.update_agent import _clean_tool_definitions
        # Place noise BEFORE the @tool block so the in_tool branch never sees them.
        defs = (
            "async def _hidden_helper():\n"
            "    pass\n"
            'if __name__ == "__main__":\n'
            "    app.run()\n"
            "import json\n"
            "@tool\n"
            'def foo() -> str:\n'
            '    """F."""\n'
            "    return ''\n"
        )
        result = _clean_tool_definitions(defs)
        assert "@tool" in result
        assert "def foo" in result
        # async def _hidden_helper / app.run / if __name__ are stripped at top level
        assert "_hidden_helper" not in result
        assert "__main__" not in result
        assert "app.run()" not in result

    def test_keeps_imports_outside_tool(self):
        from tools.update_agent import _clean_tool_definitions
        defs = (
            "import json\n"
            "from typing import Any\n"
            "@tool\n"
            'def bar() -> str:\n'
            '    """B."""\n'
            "    return ''\n"
        )
        result = _clean_tool_definitions(defs)
        assert "import json" in result
        assert "from typing" in result
        assert "def bar" in result

    def test_keeps_tool_when_decorator_above_def(self):
        from tools.update_agent import _clean_tool_definitions
        defs = (
            "@tool\n"
            'def alpha(x: str = "") -> str:\n'
            '    """A."""\n'
            "    return x\n"
        )
        assert "def alpha" in _clean_tool_definitions(defs)


# ── 2. _default_welcome (en + zh) ─────────────────────────────────────────────

class TestDefaultWelcome:
    def test_default_welcome_en(self, monkeypatch):
        from tools import _scope
        from tools.update_agent import _default_welcome
        monkeypatch.setattr(_scope, "_creator_language", "en", raising=False)
        result = _default_welcome("Bot", "An assistant")
        assert "I'm Bot" in result
        assert "An assistant" in result

    def test_default_welcome_en_no_description(self, monkeypatch):
        from tools import _scope
        from tools.update_agent import _default_welcome
        monkeypatch.setattr(_scope, "_creator_language", "en", raising=False)
        result = _default_welcome("Bot", "")
        assert result == "I'm Bot."

    def test_default_welcome_zh(self, monkeypatch):
        from tools import _scope
        from tools.update_agent import _default_welcome
        monkeypatch.setattr(_scope, "_creator_language", "zh-CN", raising=False)
        result = _default_welcome("机器人", "数据分析师")
        assert "我是 机器人" in result
        assert "数据分析师" in result

    def test_default_welcome_zh_no_description(self, monkeypatch):
        from tools import _scope
        from tools.update_agent import _default_welcome
        monkeypatch.setattr(_scope, "_creator_language", "zh", raising=False)
        result = _default_welcome("机器人", "")
        assert result == "我是 机器人。"


# ── 3. Staging-key happy path ─────────────────────────────────────────────────

class TestStagingKeyMerge:
    def test_staging_overrides_args(self, monkeypatch):
        """Staging JSON values override the explicit args (current behavior)."""
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {
            "name": "MyAgent",
            "description": "from staging",
            "system_prompt": "STAGING PROMPT",
            "tool_definitions": '@tool\ndef updated() -> str:\n    """U."""\n    return ""',
            "tool_names": "updated",
            "welcome_message": "hello!",
            "suggestions": ["a", "b"],
            "supports_images": True,
            "mcp_targets": ["cloudwatch"],
            "workspace_id": "ws-test",
            "skills": [],
        }

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_resolve_mcp_endpoints",
                            lambda targets: [{"type": "runtime", "name": t,
                                              "target_name": t, "auth": "runtime"}
                                             for t in targets])

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing", "workspace_id": "ws-test"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-existing",
                staging_key="staging/x.json",
            ))

        assert result["status"] == "UPDATING"
        assert result["action"] == "redeployed"
        ctrl.update_agent_runtime.assert_called_once()
        # description in DDB pulls from explicit arg first, but here it's empty
        # so it falls back to existing — the put_object holds the merged metadata
        put_calls = [c for c in s3.put_object.call_args_list if c.kwargs.get("Key", "").endswith("metadata.json")]
        assert put_calls
        body = json.loads(put_calls[0].kwargs["Body"].decode())
        assert body["description"] == "from staging"
        assert body["welcome_message"] == "hello!"
        assert body["suggestions"] == ["a", "b"]
        assert body["supports_images"] is True
        assert body["mcp_targets"] == ["cloudwatch"]

    def test_staging_read_failure_returns_error(self, monkeypatch):
        from tools import update_agent as mod

        s3 = MagicMock()
        s3.get_object.side_effect = Exception("staging missing")

        with patch("boto3.client", return_value=s3), \
             patch("boto3.resource"):
            result = json.loads(mod.update_agent(
                agent_id="rt-1",
                staging_key="staging/missing.json",
            ))

        assert "error" in result
        assert "staging" in result["error"].lower()

    def test_staging_suggestions_list_joined(self, monkeypatch):
        """List-shaped suggestions get joined to '|'-separated and parsed back."""
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {"suggestions": ["one", "two", "three"], "workspace_id": "ws-test"}

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_resolve_mcp_endpoints", lambda targets: [])

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", staging_key="staging/x.json")

        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        assert body["suggestions"] == ["one", "two", "three"]

    def test_staging_mcp_targets_list_joined_to_csv(self, monkeypatch):
        """A list of MCP targets is joined with comma in the merge step."""
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {
            "mcp_targets": ["cloudwatch", "iam"],
            "workspace_id": "ws-test",
        }

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_resolve_mcp_endpoints",
                            lambda t: [{"type": "runtime", "name": x,
                                        "target_name": x, "auth": "runtime"} for x in t])

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):
            mod.update_agent(agent_id="rt-existing", staging_key="staging/x.json")

        # both targets ended up in mcp_targets (list-form)
        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        assert body["mcp_targets"] == ["cloudwatch", "iam"]


# ── 4. MCP policy enforcement ─────────────────────────────────────────────────

class TestMCPPolicyEnforcement:
    def test_denied_targets_abort(self, monkeypatch):
        from tools import update_agent as mod

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(
            mod, "_get_workspace_mcp_policy",
            lambda ws: {"mode": "allowlist", "allowedTargets": ["good"]},
        )

        meta = _existing_metadata()
        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-1"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-1",
                agent_name="MyAgent",
                mcp_targets="bad-one",
            ))

        assert "error" in result
        assert "bad-one" in result["error"]
        assert "not allowed" in result["error"].lower()
        # update_agent_runtime must NOT have been called
        ctrl.update_agent_runtime.assert_not_called()


# ── 5. Role gating: editor required ──────────────────────────────────────────

class TestRoleGating:
    def test_viewer_blocked(self, monkeypatch):
        from tools import update_agent as mod

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        meta = _existing_metadata()
        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=(None, {"error": "viewer cannot update"})):

            result = json.loads(mod.update_agent(
                agent_id="rt-1",
                agent_name="MyAgent",
            ))

        assert "error" in result
        ctrl.update_agent_runtime.assert_not_called()


# ── 6. Field updates ──────────────────────────────────────────────────────────

class TestFieldUpdates:
    def test_explicit_fields_override_existing_metadata(self, monkeypatch):
        from tools import update_agent as mod

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        meta = _existing_metadata()
        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-existing",
                agent_name="MyAgent",
                description="brand new",
                display_name="Pretty",
                system_prompt="NEW PROMPT",
                tool_names="hello,world",
                tool_definitions='@tool\ndef hello() -> str:\n    """."""\n    return ""',
                welcome_message="welcome!",
                suggestions="A|B|C",
                supports_images=True,
            ))

        assert result["status"] == "UPDATING"
        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        assert body["description"] == "brand new"
        assert body["display_name"] == "Pretty"
        assert body["welcome_message"] == "welcome!"
        assert body["suggestions"] == ["A", "B", "C"]
        assert body["supports_images"] is True
        assert body["tools"] == ["hello", "world"]
        assert "NEW PROMPT" in body["system_prompt"]

    def test_blank_fields_keep_existing_metadata(self, monkeypatch):
        """When update_agent is called with no explicit values, existing metadata wins."""
        from tools import update_agent as mod

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_resolve_mcp_endpoints", lambda targets: [])

        meta = _existing_metadata(
            description="old desc",
            display_name="OldDisplay",
            welcome_message="old welc",
            suggestions=["x", "y"],
            tool_definitions='@tool\ndef t() -> str:\n    """."""\n    return ""',
            tools=["t"],
            tool_names=["t"],
        )
        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        # All fall back to existing
        assert body["description"] == "old desc"
        assert body["display_name"] == "OldDisplay"
        assert body["welcome_message"] == "old welc"
        assert body["suggestions"] == ["x", "y"]
        # extra_env_vars and linked_agents preserved (set by sibling tools)
        assert body["extra_env_vars"] == {"FOO": "bar"}
        assert body["linked_agents"] == ["rt-other"]

    def test_empty_welcome_falls_back_to_default(self, monkeypatch):
        """When welcome_message is empty in both args and metadata, default welcome is generated."""
        from tools import update_agent as mod

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        meta = _existing_metadata(welcome_message="", description="A bot")
        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        assert "I'm MyAgent" in body["welcome_message"]


# ── 7. Skills handling ────────────────────────────────────────────────────────

class TestSkills:
    def test_staging_skills_md_fetched_from_s3(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {
            "skills": [
                {"id": "skill-1", "name": "Skill1", "description": "first",
                 "contentHash": "h1"},
                {"id": "skill-2", "name": "Skill2", "description": "second",
                 "contentHash": "h2"},
            ],
            "workspace_id": "ws-test",
        }

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        captured_skills_data = []

        def fake_build_skill_prompt_section(data):
            captured_skills_data.append(data)
            return "\n## Skills\n" if data else ""

        monkeypatch.setattr(mod, "build_skill_prompt_section", fake_build_skill_prompt_section)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-existing",
                staging_key="staging/x.json",
            ))

        assert result["status"] == "UPDATING"
        # build_skill_prompt_section was called with both skills + their fetched content
        assert len(captured_skills_data) == 1
        sd = captured_skills_data[0]
        assert len(sd) == 2
        assert sd[0]["name"] == "Skill1"
        assert sd[0]["skill_md_content"] == "# Skill body"
        assert sd[1]["name"] == "Skill2"

    def test_skill_md_fetch_failure_logs_warning(self, monkeypatch, capsys):
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {
            "skills": [{"id": "broken", "name": "B", "description": "d",
                        "contentHash": "h"}],
            "workspace_id": "ws-test",
        }

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged, fail_skill_md=True)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-existing",
                staging_key="staging/x.json",
            ))

        assert result["status"] == "UPDATING"
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "broken" in captured.err

    def test_no_staging_skills_falls_back_to_metadata_skills(self, monkeypatch):
        """When no staging is given and metadata has skills, keep them and rebuild prompt."""
        from tools import update_agent as mod

        meta = _existing_metadata(skills=[
            {"id": "kept", "name": "Kept", "description": "stays",
             "contentHash": "h"},
        ])

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        captured = []
        monkeypatch.setattr(mod, "build_skill_prompt_section",
                            lambda data: (captured.append(data), "")[1] or "")

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        # captured[-1] is the data passed to build_skill_prompt_section
        assert len(captured) == 1
        assert captured[0][0]["name"] == "Kept"

        # Skills are preserved in the new metadata
        meta_calls = [c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("metadata.json")]
        body = json.loads(meta_calls[0].kwargs["Body"].decode())
        assert body["skills"][0]["name"] == "Kept"


# ── 8. Builtin tool injection ─────────────────────────────────────────────────

class TestBuiltinInjection:
    def test_builtin_tool_injected_when_missing_from_definitions(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata(tool_definitions="", tools=[])

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(
            mod, "_get_builtin_code",
            lambda name: '@tool\ndef web_search() -> str:\n    """."""\n    return ""'
            if name == "web_search" else None,
        )

        captured = {}

        def fake_validate(main_py, tools_py, prompt_txt, config_json):
            captured["tools_py"] = tools_py
            return {"valid": True, "errors": []}

        monkeypatch.setattr(mod, "validate_agent_files", fake_validate)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(
                agent_id="rt-existing",
                agent_name="MyAgent",
                tool_names="web_search",
            )

        assert "web_search" in captured["tools_py"]


# ── 9. KB injection ──────────────────────────────────────────────────────────

class TestKBInjection:
    def test_kb_injected_from_staging(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        staged = {"knowledge_bases": ["kb-1"], "workspace_id": "ws-test"}

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        # Replace tools.kb_inject so resolve_kb_bindings/build_kb_injection are deterministic
        kb_mod = types.ModuleType("tools.kb_inject")
        kb_mod.resolve_kb_bindings = lambda ws, ids: [{"id": "kb-1", "name": "KB"}]
        kb_mod.build_kb_injection = (
            lambda recs: '@tool\ndef kb_retrieve(q: str = "") -> str:\n    """."""\n    return ""'
        )
        sys.modules["tools.kb_inject"] = kb_mod

        captured = {}

        def fake_validate(main_py, tools_py, prompt_txt, config_json):
            captured["tools_py"] = tools_py
            captured["config"] = json.loads(config_json)
            return {"valid": True, "errors": []}

        monkeypatch.setattr(mod, "validate_agent_files", fake_validate)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, staged)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", staging_key="staging/x.json")

        assert "kb_retrieve" in captured["tools_py"]
        assert "kb_retrieve" in captured["config"]["tool_names"]

    def test_kb_injected_from_ddb_when_no_staging(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        # Make it so that the explicit boto3.client('dynamodb') get_item returns kb_ids
        ddb_get = {"Item": {"knowledge_bases": {"SS": ["kb-1"]}}}

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        kb_mod = types.ModuleType("tools.kb_inject")
        kb_mod.resolve_kb_bindings = lambda ws, ids: [{"id": "kb-1", "name": "KB"}]
        kb_mod.build_kb_injection = (
            lambda recs: '@tool\ndef kb_retrieve(q: str = "") -> str:\n    """."""\n    return ""'
        )
        sys.modules["tools.kb_inject"] = kb_mod

        captured = {}

        def fake_validate(main_py, tools_py, prompt_txt, config_json):
            captured["tools_py"] = tools_py
            return {"valid": True, "errors": []}

        monkeypatch.setattr(mod, "validate_agent_files", fake_validate)

        # Explicitly set workspace_id for KB injection — required for kb_inject
        # to fire (it needs both kb_ids and workspace_id).
        monkeypatch.setattr(mod, "_workspace_id", "ws-test", raising=False)
        # update_agent reads via __import__('tools.create_agent', fromlist=['_workspace_id'])
        from tools import create_agent as ca_mod
        monkeypatch.setattr(ca_mod, "_workspace_id", "ws-test", raising=False)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None, dynamodb_get_item=ddb_get)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        assert "kb_retrieve" in captured["tools_py"]


# ── 10. Validation failure ───────────────────────────────────────────────────

class TestValidationFailure:
    def test_validate_files_failure_returns_error(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "validate_agent_files",
                            lambda *a, **kw: {"valid": False,
                                              "errors": ["Python syntax error"]})

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            result = json.loads(mod.update_agent(
                agent_id="rt-existing",
                agent_name="MyAgent",
                system_prompt="NEW",
            ))

        assert "error" in result
        assert "validation" in result["error"].lower()
        ctrl.update_agent_runtime.assert_not_called()


# ── 11. Existing config preservation ─────────────────────────────────────────

class TestExistingConfigPreservation:
    def test_existing_mcp_config_preserved_when_targets_empty(self, monkeypatch):
        """When no mcp_targets in args, existing config.json or metadata is reused."""
        from tools import update_agent as mod

        meta = _existing_metadata(mcp_targets=["kept-target"])

        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(
            mod, "_resolve_mcp_endpoints",
            lambda t: [{"type": "runtime", "name": x,
                        "target_name": x, "auth": "runtime"} for x in t],
        )

        # Build s3 mock that returns an existing config.json with mcp settings
        cfg = {"mcp_targets": ["from-config"], "mcp_endpoints": [
            {"type": "runtime", "name": "from-config", "target_name": "from-config", "auth": "runtime"}
        ]}

        s3 = MagicMock()

        def get_object(Bucket=None, Key=None, **kw):
            if Key.endswith("/metadata.json"):
                return {"Body": MagicMock(read=lambda: json.dumps(meta).encode())}
            if Key.endswith("/config.json"):
                return {"Body": MagicMock(read=lambda: json.dumps(cfg).encode())}
            raise Exception("nope")

        s3.get_object.side_effect = get_object
        s3.put_object.return_value = {}

        ctrl = MagicMock()
        ddb_low = MagicMock()

        def cf(svc, **kw):
            if svc == "s3":
                return s3
            if svc == "bedrock-agentcore-control":
                return ctrl
            return ddb_low

        rf = MagicMock()
        rf.Table.return_value = MagicMock()

        captured = {}

        def fake_validate(main_py, tools_py, prompt_txt, config_json):
            captured["config"] = json.loads(config_json)
            return {"valid": True, "errors": []}

        monkeypatch.setattr(mod, "validate_agent_files", fake_validate)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        # mcp_targets came from config.json, not metadata
        assert captured["config"]["mcp_targets"] == ["from-config"]


# ── 12. Workspace fallback (no staging) ──────────────────────────────────────

class TestWorkspaceFallback:
    def test_no_staging_uses_module_workspace(self, monkeypatch):
        """update_agent reads tools.create_agent._workspace_id when no staging."""
        from tools import create_agent as ca_mod, update_agent as mod

        # Set the module-level workspace
        monkeypatch.setattr(ca_mod, "_workspace_id", "ws-fallback", raising=False)

        captured_ws = []

        def role_arn(ws):
            captured_ws.append(ws)
            return "arn:role"

        meta = _existing_metadata()
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})
        monkeypatch.setattr(mod, "_get_agent_role_arn", role_arn)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        # Workspace from create_agent module attr was used for the role lookup
        assert "ws-fallback" in captured_ws


# ── 13. Result shape & DDB update ─────────────────────────────────────────────

class TestDDBUpdate:
    def test_ddb_update_item_called_with_expected_fields(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        ctrl = MagicMock()
        s3, _, table, _, cf, rf = _patch_boto(meta, None, control=ctrl)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(
                agent_id="rt-existing",
                agent_name="MyAgent",
                description="x",
                tool_names="hello",
            )

        assert table.update_item.called
        kwargs = table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"agentId": "rt-existing"}
        expr = kwargs["UpdateExpression"]
        assert "description = :desc" in expr
        assert "display_name = :dn" in expr
        assert "tool_names = :tn" in expr  # because tool_names was supplied
        # also writes system_prompt + welcome + suggestions + mcp + skills
        assert "system_prompt = :sp" in expr
        assert "welcome_message = :wm" in expr

    def test_ddb_skips_tool_names_field_when_no_tools(self, monkeypatch):
        """If final tool_names list is empty, no `tool_names = :tn` in update."""
        from tools import update_agent as mod

        meta = _existing_metadata(
            tools=[],
            tool_names=[],
            tool_definitions="",
        )
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")

        kwargs = table.update_item.call_args.kwargs
        assert "tool_names = :tn" not in kwargs["UpdateExpression"]


# ── 14. Mirror system_prompt.txt and tool_definitions.py ─────────────────────

class TestMirrorFiles:
    def test_system_prompt_and_tools_files_mirrored(self, monkeypatch):
        from tools import update_agent as mod

        meta = _existing_metadata()
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            mod.update_agent(
                agent_id="rt-existing",
                agent_name="MyAgent",
                system_prompt="MY NEW PROMPT",
                tool_definitions='@tool\ndef thing() -> str:\n    """."""\n    return ""',
                tool_names="thing",
            )

        keys_written = [c.kwargs["Key"] for c in s3.put_object.call_args_list]
        assert any(k.endswith("metadata.json") for k in keys_written)
        assert any(k.endswith("system_prompt.txt") for k in keys_written)
        assert any(k.endswith("tool_definitions.py") for k in keys_written)

        sp_call = next(c for c in s3.put_object.call_args_list
                   if c.kwargs.get("Key", "").endswith("system_prompt.txt"))
        assert b"MY NEW PROMPT" in sp_call.kwargs["Body"]

        tools_call = next(c for c in s3.put_object.call_args_list
                      if c.kwargs.get("Key", "").endswith("tool_definitions.py"))
        assert b"def thing" in tools_call.kwargs["Body"]


# ── 15. Never-raise contract ─────────────────────────────────────────────────

class TestNeverRaise:
    def test_unexpected_exception_during_deploy_propagates_or_returns_json(self, monkeypatch):
        """If the upload step raises, update_agent currently lets it bubble.

        This documents the current contract; if the policy changes the test
        will catch it. Either way, no half-deploys leak: control.update_agent_runtime
        is not called.
        """
        from tools import update_agent as mod

        meta = _existing_metadata()
        _patch_deploy_chain(monkeypatch, mod)
        monkeypatch.setattr(mod, "_get_workspace_mcp_policy", lambda ws: {"mode": "all"})

        def boom(*a, **kw):
            raise RuntimeError("upload exploded")

        monkeypatch.setattr(mod, "upload_deployment", boom)

        s3, ctrl, table, ddb_low, cf, rf = _patch_boto(meta, None)

        with patch("boto3.client", side_effect=cf), \
             patch("boto3.resource", return_value=rf), \
             patch("tools._scope.ensure_agent_in_workspace",
                   return_value=({"agentId": "rt-existing"}, None)):

            try:
                result = mod.update_agent(agent_id="rt-existing", agent_name="MyAgent")
            except RuntimeError:
                # Current behavior: bubbles. The runtime never got an update call.
                ctrl.update_agent_runtime.assert_not_called()
                return

            # If the contract changes to never-raise, we get a JSON dict here.
            parsed = json.loads(result)
            assert "error" in parsed
            ctrl.update_agent_runtime.assert_not_called()
