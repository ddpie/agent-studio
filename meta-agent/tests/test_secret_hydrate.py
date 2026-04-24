"""Tests for the cold-start secret hydration logic embedded in
agent_template_v2.MAIN_PY_TEMPLATE.

The hydrate function is rendered as a string inside the template, so we
can't import it directly. We extract the function body via AST and
exec it inside a controlled namespace with a fake boto3, then assert
that AGENT_STUDIO_SECRET_ARNS → os.environ[KEY] injection works and
failures don't kill startup.

Why not pull the function into a standalone module that both deploy and
test import? Because the template is flattened into main.py at deploy
time — the sub-agent runtime doesn't have access to the Meta-Agent's
package layout. Embedding keeps the runtime single-file. To test the
real code we parse the same string the deploy step writes.
"""
import ast
import os
import sys
import types
from unittest.mock import MagicMock


# ── Minimal module stubs (same pattern as sibling tests) ──────────────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000",
    "S3_BUCKET": "b",
    "AGENT_ROLE_ARN": "arn:aws:iam::000:role/r",
    "BASE_DEPLOYMENT_KEY": "k",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "k",
    "AGENTS_TABLE": "agent-studio-agents",
    "TOOLS_TABLE": "agent-studio-tools",
    "MODEL_ID": "mock",
    "MCP_GATEWAY_URL": "",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/r"},
    "DEFAULT_PERMISSION_TIER": "readonly",
    "CODE_INTERPRETER_ID": "",
    "BROWSER_ID": "",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


def _extract_hydrate_source() -> str:
    """Parse MAIN_PY_TEMPLATE and return the source of _hydrate_secrets_from_arns.

    Uses AST so we don't depend on lexical indentation or line numbers
    holding steady as the template grows.
    """
    from templates.agent_template_v2 import MAIN_PY_TEMPLATE
    tree = ast.parse(MAIN_PY_TEMPLATE)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_hydrate_secrets_from_arns":
            return ast.get_source_segment(MAIN_PY_TEMPLATE, node)
    raise AssertionError("_hydrate_secrets_from_arns not found in template")


def _build_fake_boto3(secrets_by_arn: dict, *, raise_on: set | None = None):
    """Build a fake boto3 module whose SecretsManager client returns secrets_by_arn."""
    raise_on = raise_on or set()
    fake_sm = MagicMock()

    def _get_secret_value(SecretId):
        if SecretId in raise_on:
            raise RuntimeError(f"simulated failure for {SecretId}")
        if SecretId not in secrets_by_arn:
            raise KeyError(SecretId)
        return {"SecretString": secrets_by_arn[SecretId]}

    fake_sm.get_secret_value = _get_secret_value

    def _client(service, region_name=None):
        assert service == "secretsmanager"
        return fake_sm

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = _client
    return fake_boto3


def _run_hydrate(env: dict, boto3_mod) -> tuple[int, dict]:
    """Exec the real hydrate function in a sandboxed namespace and return (loaded, os.environ snapshot)."""
    src = _extract_hydrate_source()

    # Per-call fake os with only the attrs the hydrate body uses
    # (environ.get / environ[key] = ... / environ containment check).
    fake_os = types.ModuleType("os")
    fake_os.environ = dict(env)

    # Stub sys so stderr prints don't clutter test output
    fake_sys = types.ModuleType("sys")
    fake_sys.stderr = MagicMock()

    # The exec'd function expects module-level bindings _os, and imports
    # boto3 + concurrent.futures internally. Inject fakes via sys.modules
    # for the import statements inside the function body.
    saved_boto3 = sys.modules.get("boto3")
    sys.modules["boto3"] = boto3_mod
    try:
        ns = {"_os": fake_os, "__builtins__": __builtins__}
        exec(src, ns)
        loaded = ns["_hydrate_secrets_from_arns"]()
    finally:
        if saved_boto3 is not None:
            sys.modules["boto3"] = saved_boto3
        else:
            sys.modules.pop("boto3", None)
    return loaded, fake_os.environ


# ── Tests ────────────────────────────────────────────────────────────────


def test_hydrate_empty_env_returns_zero():
    loaded, env = _run_hydrate({}, _build_fake_boto3({}))
    assert loaded == 0
    assert env == {}


def test_hydrate_injects_secrets_from_arn_list():
    # Realistic ARN shape: agent-studio/{ws}/{agent}/{KEY}-{6char suffix}
    arn_a = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/DataAnalyst-X/FEISHU_USER_ACCESS_TOKEN-aBc123"
    )
    arn_b = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/DataAnalyst-X/FEISHU_APP_ID-xY9Zab"
    )
    env = {
        "AGENT_STUDIO_SECRET_ARNS": f"{arn_a},{arn_b}",
        "AGENT_STUDIO_REGION": "us-east-1",
    }
    boto3_mod = _build_fake_boto3({arn_a: "u-secret-token", arn_b: "cli_123"})
    loaded, out_env = _run_hydrate(env, boto3_mod)
    assert loaded == 2
    assert out_env["FEISHU_USER_ACCESS_TOKEN"] == "u-secret-token"
    assert out_env["FEISHU_APP_ID"] == "cli_123"


def test_hydrate_single_failure_is_nonfatal():
    good = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/A/GOOD-abcdef"
    )
    bad = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/A/BAD-123456"
    )
    env = {"AGENT_STUDIO_SECRET_ARNS": f"{good},{bad}"}
    boto3_mod = _build_fake_boto3({good: "ok", bad: "ignored"}, raise_on={bad})
    loaded, out_env = _run_hydrate(env, boto3_mod)
    # GOOD hydrated, BAD swallowed
    assert loaded == 1
    assert out_env["GOOD"] == "ok"
    assert "BAD" not in out_env


def test_hydrate_does_not_overwrite_existing_env():
    # If a key is already in os.environ (e.g. set by the sub-agent's
    # deployment env-vars), hydrate must NOT stomp it. This guarantees
    # that explicit env config wins over Secrets Manager, which matches
    # standard 12-factor layering (explicit > defaults).
    arn = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/A/API_KEY-aaaaaa"
    )
    env = {
        "AGENT_STUDIO_SECRET_ARNS": arn,
        "API_KEY": "explicit-override",
    }
    boto3_mod = _build_fake_boto3({arn: "from-secrets-manager"})
    loaded, out_env = _run_hydrate(env, boto3_mod)
    assert out_env["API_KEY"] == "explicit-override"
    assert loaded == 0


def test_hydrate_handles_malformed_arn_list():
    # Blank entries / extra commas / whitespace should be tolerated —
    # the env var is produced via "",".join()" in deploy.py but an
    # empty list still emits "" and a single-entry list has no comma.
    env = {"AGENT_STUDIO_SECRET_ARNS": " , , "}
    boto3_mod = _build_fake_boto3({})
    loaded, out_env = _run_hydrate(env, boto3_mod)
    assert loaded == 0


def test_hydrate_boto3_import_failure_is_nonfatal():
    # If boto3 somehow isn't in the deployment (shouldn't happen, but
    # defensive): hydrate must return 0 rather than crash.
    arn = (
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:"
        "agent-studio/ws-1/A/X-aaaaaa"
    )
    env = {"AGENT_STUDIO_SECRET_ARNS": arn}
    # Simulate ImportError by passing a module that raises on attribute access
    broken = types.ModuleType("boto3")
    def _broken_client(*a, **kw):
        raise ImportError("boto3 missing")
    broken.client = _broken_client
    loaded, out_env = _run_hydrate(env, broken)
    # No keys injected, no exception propagated
    assert "X" not in out_env
