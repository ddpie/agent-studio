"""Tests for the cold-start secret hydration + CI-forwarding logic
embedded in agent_template_v2.BUILTIN_TOOLS_CODE.

Both _hydrate_secrets_from_arns() and _secret_env_prefix_for_ci() are
rendered as strings inside the template so agent zips stay
single-file. We extract them via AST and exec into a controlled
namespace with a fake boto3, then assert:

  - AGENT_STUDIO_SECRET_ARNS → os.environ[KEY] injection works
  - failures don't kill startup
  - _SECRET_ENV_KEYS records exactly what hydrate loaded
  - _secret_env_prefix_for_ci emits quote-safe setdefault lines for
    those keys (and only those keys — no OTEL / PATH leakage)

Why not pull the functions into a standalone importable module? The
template is flattened into builtin_tools.py at deploy time; the
agent runtime doesn't have access to the Meta-Agent's package
layout. Embedding keeps runtime single-file; we parse the same string
the deploy step writes.
"""

import ast
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


def _extract_fn_sources() -> tuple[str, str]:
    """Parse BUILTIN_TOOLS_CODE and return (hydrate_src, prefix_src).

    Uses AST so we don't depend on lexical indentation or line numbers
    holding steady as the template grows.
    """
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE

    tree = ast.parse(BUILTIN_TOOLS_CODE)
    hydrate_src = prefix_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "_hydrate_secrets_from_arns":
                hydrate_src = ast.get_source_segment(BUILTIN_TOOLS_CODE, node)
            elif node.name == "_secret_env_prefix_for_ci":
                prefix_src = ast.get_source_segment(BUILTIN_TOOLS_CODE, node)
    if not hydrate_src:
        raise AssertionError("_hydrate_secrets_from_arns not found in template")
    if not prefix_src:
        raise AssertionError("_secret_env_prefix_for_ci not found in template")
    return hydrate_src, prefix_src


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


def _run_hydrate(env: dict, boto3_mod) -> tuple[int, dict, list, callable]:
    """Exec hydrate + prefix in a sandboxed namespace.

    Returns ``(loaded, os.environ snapshot, _SECRET_ENV_KEYS, prefix_fn)``
    so individual tests can inspect post-hydrate state or call the
    prefix builder against the same namespace.
    """
    hydrate_src, prefix_src = _extract_fn_sources()

    fake_os = types.ModuleType("os")
    fake_os.environ = dict(env)

    # builtin_tools binds boto3 as _boto3 at module top-level. We mimic
    # that by pre-populating the namespace rather than letting the
    # function's own import statement run (it doesn't — hydrate uses the
    # outer _boto3 symbol).
    ns = {
        "_os": fake_os,
        "_boto3": boto3_mod,
        "_REGION": "us-east-1",
        "_SECRET_ENV_KEYS": [],
        "__builtins__": __builtins__,
    }
    exec(hydrate_src, ns)
    exec(prefix_src, ns)
    loaded = ns["_hydrate_secrets_from_arns"]()
    return loaded, fake_os.environ, ns["_SECRET_ENV_KEYS"], ns["_secret_env_prefix_for_ci"]


# ── Tests ────────────────────────────────────────────────────────────────


def test_hydrate_empty_env_returns_zero():
    loaded, out_env, keys, prefix_fn = _run_hydrate({}, _build_fake_boto3({}))
    assert loaded == 0
    assert out_env == {}
    assert keys == []
    # No secrets → no prefix (empty string is the signal to skip prepending).
    assert prefix_fn() == ""


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
    loaded, out_env, _keys, _prefix = _run_hydrate(env, boto3_mod)
    assert loaded == 2
    assert out_env["FEISHU_USER_ACCESS_TOKEN"] == "u-secret-token"
    assert out_env["FEISHU_APP_ID"] == "cli_123"


def test_hydrate_single_failure_is_nonfatal():
    good = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/GOOD-abcdef"
    bad = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/BAD-123456"
    env = {"AGENT_STUDIO_SECRET_ARNS": f"{good},{bad}"}
    boto3_mod = _build_fake_boto3({good: "ok", bad: "ignored"}, raise_on={bad})
    loaded, out_env, _keys, _prefix = _run_hydrate(env, boto3_mod)
    # GOOD hydrated, BAD swallowed
    assert loaded == 1
    assert out_env["GOOD"] == "ok"
    assert "BAD" not in out_env


def test_hydrate_does_not_overwrite_existing_env():
    # If a key is already in os.environ (e.g. set by the agent's
    # deployment env-vars), hydrate must NOT stomp it. This guarantees
    # that explicit env config wins over Secrets Manager, which matches
    # standard 12-factor layering (explicit > defaults).
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/API_KEY-aaaaaa"
    env = {
        "AGENT_STUDIO_SECRET_ARNS": arn,
        "API_KEY": "explicit-override",
    }
    boto3_mod = _build_fake_boto3({arn: "from-secrets-manager"})
    loaded, out_env, _keys, _prefix = _run_hydrate(env, boto3_mod)
    assert out_env["API_KEY"] == "explicit-override"
    assert loaded == 0


def test_hydrate_handles_malformed_arn_list():
    # Blank entries / extra commas / whitespace should be tolerated —
    # the env var is produced via "",".join()" in deploy.py but an
    # empty list still emits "" and a single-entry list has no comma.
    env = {"AGENT_STUDIO_SECRET_ARNS": " , , "}
    boto3_mod = _build_fake_boto3({})
    loaded, out_env, _keys, _prefix = _run_hydrate(env, boto3_mod)
    assert loaded == 0


def test_hydrate_boto3_import_failure_is_nonfatal():
    # If boto3 somehow isn't in the deployment (shouldn't happen, but
    # defensive): hydrate must return 0 rather than crash.
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/X-aaaaaa"
    env = {"AGENT_STUDIO_SECRET_ARNS": arn}
    # Simulate ImportError by passing a module that raises on attribute access
    broken = types.ModuleType("boto3")

    def _broken_client(*a, **kw):
        raise ImportError("boto3 missing")

    broken.client = _broken_client
    loaded, out_env, _keys, _prefix = _run_hydrate(env, broken)
    # No keys injected, no exception propagated
    assert "X" not in out_env


# ── Tests: CI forwarding prefix ──────────────────────────────────────────


def test_prefix_records_only_hydrated_keys_not_all_env():
    # This is the whole point of the _SECRET_ENV_KEYS allow-list: env
    # vars the user did NOT put in Agent Secrets (PATH, OTEL, etc.)
    # must not be copied into the sandbox. Leaking e.g. OTEL_RESOURCE_
    # ATTRIBUTES into user scripts isn't sensitive, but forwarding
    # every env across the boundary would 10x the executeCode payload
    # and could trip AgentCore's size limits.
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/TOKEN-abcdef"
    env = {
        "AGENT_STUDIO_SECRET_ARNS": arn,
        "PATH": "/usr/bin",
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=test",
    }
    boto3_mod = _build_fake_boto3({arn: "secret-value"})
    _, _, keys, prefix_fn = _run_hydrate(env, boto3_mod)
    assert keys == ["TOKEN"]
    prefix = prefix_fn()
    assert "TOKEN" in prefix
    assert "secret-value" in prefix
    assert "PATH" not in prefix
    assert "OTEL_RESOURCE_ATTRIBUTES" not in prefix


def test_prefix_uses_repr_for_quote_safety():
    # If a token has an embedded quote / backslash / newline, a naive
    # f-string would break the emitted Python. repr() handles all of
    # these. Test with a value that exercises each case.
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/TRICKY-aaaaaa"
    env = {"AGENT_STUDIO_SECRET_ARNS": arn}
    hostile = "a'b\"c\\d\ne"
    boto3_mod = _build_fake_boto3({arn: hostile})
    _, _, _, prefix_fn = _run_hydrate(env, boto3_mod)
    prefix = prefix_fn()
    # The emitted snippet must be valid Python and, when exec'd, must
    # reproduce the original value in os.environ.
    ns = {"_os": types.ModuleType("os")}
    ns["_os"].environ = {}
    exec(prefix, ns)
    assert ns["_os"].environ["TRICKY"] == hostile


def test_prefix_uses_setdefault_not_assignment():
    # setdefault means: if the sandbox already has a value for this
    # key (user set it explicitly via run_command before calling a
    # skill), don't stomp it. Prevents surprising behavior where a
    # user temporarily overrides a token for debugging and the next
    # skill call silently reverts it.
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/TOKEN-zzzzzz"
    env = {"AGENT_STUDIO_SECRET_ARNS": arn}
    boto3_mod = _build_fake_boto3({arn: "from-hydrate"})
    _, _, _, prefix_fn = _run_hydrate(env, boto3_mod)
    prefix = prefix_fn()
    assert "setdefault" in prefix
    assert "os.environ['TOKEN'] =" not in prefix  # no direct assignment


def test_prefix_skips_keys_with_empty_value():
    # If hydrate set the key but then the value was somehow cleared
    # (defensive edge case), the prefix builder skips it rather than
    # emitting setdefault("", "") which is a no-op but adds noise.
    arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:agent-studio/ws-1/A/FULL-aaaaaa"
    env = {"AGENT_STUDIO_SECRET_ARNS": arn}
    boto3_mod = _build_fake_boto3({arn: "val"})
    _, out_env, keys, prefix_fn = _run_hydrate(env, boto3_mod)
    out_env["FULL"] = ""
    prefix = prefix_fn()
    assert "FULL" not in prefix
