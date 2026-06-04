"""End-to-end smoke test for Sprint 1 shared sandbox (Code Interpreter + Browser).

This test exercises the *account-shared* AgentCore resources provisioned
by infra/lib/constructs/agentcore-shared.ts — not the agent code
path. It proves:

  1. The CDK-managed CodeInterpreter is reachable and can execute Python.
  2. The CDK-managed Browser is reachable and surfaces a CDP endpoint.
  3. Our runtime IAM (the caller's role — ops / CI) can list/create/stop
     sessions on these resources.

It also runs two agent-process tests against an existing deployed
agent (default: `claudewatch-kGlipG6kpy`, override via
AGENT_STUDIO_E2E_SUBAGENT_ID). These are the tests that actually prove
`run_command` / `fetch_webpage` route through the shared sandbox: we
invoke the agent with a prompt, wait for the reply, then check
that a fresh CI / Browser session appeared on the shared resource
within the last 10 minutes.

Prerequisite: the agent must be deployed against the current
base/deployment.zip — i.e. post-2026-04-18 (tools/ shadow fix). Older
deployments will still use the subprocess-based run_command and won't
produce CI sessions.

Gated on AGENT_STUDIO_E2E=1 so `pytest tests/` stays fast + offline.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import boto3
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

if os.environ.get("AGENT_STUDIO_E2E", "0") != "1":
    pytest.skip("Set AGENT_STUDIO_E2E=1 to run live-AWS E2E", allow_module_level=True)

REGION = os.environ.get("AGENT_STUDIO_REGION", "us-east-1")
CI_ID = os.environ["AGENT_STUDIO_CODE_INTERPRETER_ID"]
BR_ID = os.environ["AGENT_STUDIO_BROWSER_ID"]
SUBAGENT_ID = os.environ.get("AGENT_STUDIO_E2E_SUBAGENT_ID", "claudewatch-kGlipG6kpy")


@pytest.fixture(scope="module")
def agentcore():
    return boto3.client("bedrock-agentcore", region_name=REGION)


def _find_recent(items: list[dict], age_s: int = 600) -> bool:
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    return any((now - it["createdAt"]).total_seconds() < age_s for it in items if it.get("createdAt"))


def test_shared_code_interpreter_roundtrips_python(agentcore):
    """Start a CI session, invoke executeCode with Python, expect correct stdout."""
    sess = agentcore.start_code_interpreter_session(
        codeInterpreterIdentifier=CI_ID,
        name=f"e2e-{uuid.uuid4().hex[:8]}",
        sessionTimeoutSeconds=600,
    )
    session_id = sess["sessionId"]
    assert session_id, "start_code_interpreter_session did not return a sessionId"

    try:
        resp = agentcore.invoke_code_interpreter(
            codeInterpreterIdentifier=CI_ID,
            sessionId=session_id,
            name="executeCode",
            arguments={"code": "print(6*7)", "language": "python"},
        )
        stdout, stderr, exit_code = "", "", 0
        for event in resp.get("stream", []):
            sc = (event.get("result") or {}).get("structuredContent") or {}
            stdout += sc.get("stdout", "")
            stderr += sc.get("stderr", "")
            exit_code = sc.get("exitCode", exit_code)
        assert exit_code in (0, None), f"non-zero exit; stderr={stderr!r}"
        assert "42" in stdout, f"expected 42 in stdout, got {stdout!r}"

        # Sanity check: list sessions surfaces our session.
        listed = agentcore.list_code_interpreter_sessions(
            codeInterpreterIdentifier=CI_ID,
        ).get("items", [])
        ids = {s["sessionId"] for s in listed}
        assert session_id in ids, "our session missing from list_code_interpreter_sessions"
        assert _find_recent(listed), "no recent CI sessions on shared resource"
    finally:
        try:
            agentcore.stop_code_interpreter_session(
                codeInterpreterIdentifier=CI_ID,
                sessionId=session_id,
            )
        except Exception:
            pass


def test_shared_browser_opens_cdp_stream(agentcore):
    """Start a Browser session and verify the automation stream endpoint appears."""
    sess = agentcore.start_browser_session(
        browserIdentifier=BR_ID,
        name=f"e2e-{uuid.uuid4().hex[:8]}",
        sessionTimeoutSeconds=300,
        viewPort={"width": 1280, "height": 800},
    )
    session_id = sess["sessionId"]
    assert session_id
    streams = sess.get("streams", {})
    assert "automationStream" in streams, f"no automationStream in streams={streams!r}"
    assert streams["automationStream"]["streamEndpoint"].startswith("wss://"), (
        f"CDP endpoint should be wss://, got {streams['automationStream']['streamEndpoint']!r}"
    )

    try:
        listed = agentcore.list_browser_sessions(browserIdentifier=BR_ID).get("items", [])
        ids = {s["sessionId"] for s in listed}
        assert session_id in ids, "our session missing from list_browser_sessions"
        assert _find_recent(listed), "no recent Browser sessions on shared resource"
    finally:
        try:
            agentcore.stop_browser_session(
                browserIdentifier=BR_ID,
                sessionId=session_id,
            )
        except Exception:
            pass


def _invoke_subagent(agent_id: str, prompt: str, max_attempts: int = 3) -> str:
    """Invoke agent runtime; retry on cold-start timeout."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    last_err = None
    for _attempt in range(max_attempts):
        try:
            resp = client.invoke_agent_runtime(
                agentRuntimeArn=f"arn:aws:bedrock-agentcore:{REGION}:{acct}:runtime/{agent_id}",
                qualifier="DEFAULT",
                runtimeSessionId=f"e2e-{uuid.uuid4()}".ljust(33, "x")[:64],
                payload=json.dumps({"prompt": prompt, "caller_id": "e2e-smoke"}).encode(),
            )
            return "".join(
                e.decode("utf-8", errors="ignore") for e in resp["response"] if isinstance(e, bytes)
            )
        except client.exceptions.RuntimeClientError as e:
            last_err = e
            time.sleep(15)
    raise AssertionError(f"agent {agent_id} cold-start never recovered: {last_err}")


def test_subagent_fetch_webpage_routes_through_browser(agentcore):
    """Invoke a deployed agent with a fetch_webpage prompt and confirm
    a Browser session was created on the shared resource.

    Prerequisite: the chosen agent's tools.py must use the
    tools_library/fetch_webpage.py variant (Browser-backed), not a
    user-authored custom fetch_webpage (urllib-backed). Skips if the
    current deployment has a custom version — we detect by sniffing the
    deployed tools.py for the Browser call pattern.
    """
    import io
    import zipfile

    s3 = boto3.client("s3", region_name=REGION)
    bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
    zip_bytes = s3.get_object(Bucket=bucket, Key=f"agents/{SUBAGENT_ID}/deployment.zip")["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        tools_py = z.read("tools.py").decode("utf-8", errors="ignore")
    if "start_browser_session" not in tools_py:
        pytest.skip(
            f"agent {SUBAGENT_ID} tools.py does not use Browser-backed "
            "fetch_webpage (looks like a user-authored custom variant); "
            "set AGENT_STUDIO_E2E_SUBAGENT_ID to an agent whose tools.py "
            "includes 'start_browser_session'"
        )

    baseline_before = agentcore.list_browser_sessions(browserIdentifier=BR_ID).get("items", [])
    recent_before = {s["sessionId"] for s in baseline_before if _find_recent([s], age_s=60)}

    reply = _invoke_subagent(
        SUBAGENT_ID,
        "Use fetch_webpage to fetch https://example.com and report the first 100 chars of the page text verbatim.",
    )
    assert "Example Domain" in reply, (
        f"agent did not successfully fetch example.com; tail of reply: {reply[-400:]}"
    )

    listed = agentcore.list_browser_sessions(browserIdentifier=BR_ID).get("items", [])
    new_recent = [s for s in listed if s["sessionId"] not in recent_before and _find_recent([s], age_s=120)]
    assert new_recent, (
        f"expected a new Browser session in the last 2 min on {BR_ID}, "
        f"got {len(listed)} total sessions; baseline_recent={len(recent_before)}"
    )


def test_subagent_run_command_routes_through_code_interpreter(agentcore):
    """Invoke a deployed agent with a run_command prompt and confirm
    a Code Interpreter session was created on the shared resource."""
    baseline = agentcore.list_code_interpreter_sessions(codeInterpreterIdentifier=CI_ID).get("items", [])
    recent_before = {s["sessionId"] for s in baseline if _find_recent([s], age_s=60)}

    reply = _invoke_subagent(
        SUBAGENT_ID,
        "Use run_command with language='python' and code 'print(6*7)'. Return only the numeric stdout.",
    )
    assert "42" in reply, f"expected 42 in reply tail: {reply[-400:]}"

    listed = agentcore.list_code_interpreter_sessions(codeInterpreterIdentifier=CI_ID).get("items", [])
    new_recent = [s for s in listed if s["sessionId"] not in recent_before and _find_recent([s], age_s=120)]
    assert new_recent, (
        f"expected a new Code Interpreter session in the last 2 min on {CI_ID}, "
        f"got {len(listed)} total sessions; baseline_recent={len(recent_before)}"
    )


def test_shared_resources_are_cfn_managed():
    """Confirm the IDs we're testing come from CloudFormation stack outputs —
    if the user provisioned out-of-band, later `cdk deploy` will replace
    them and break Meta-Agent env vars."""
    cfn = boto3.client("cloudformation", region_name=REGION)
    outputs = cfn.describe_stacks(StackName="AgentStudioStack")["Stacks"][0]["Outputs"]
    kv = {o["OutputKey"]: o["OutputValue"] for o in outputs}
    ci_keys = [v for k, v in kv.items() if "CodeInterpreterId" in k]
    br_keys = [v for k, v in kv.items() if "BrowserId" in k]
    assert CI_ID in ci_keys, (
        f"CODE_INTERPRETER_ID {CI_ID} is not a CFN output of AgentStudioStack "
        f"(outputs: {ci_keys}) — likely out-of-band provisioning; re-run cdk deploy"
    )
    assert BR_ID in br_keys, (
        f"BROWSER_ID {BR_ID} is not a CFN output of AgentStudioStack (outputs: {br_keys})"
    )


# ---------------------------------------------------------------------------
# Sprint 2 live-AWS guards
# ---------------------------------------------------------------------------


def test_workspace_has_eval_config():
    """After Sprint 2 Task 2, at least one workspace-scoped online eval
    config must exist. Guard against silently losing provisioning."""
    c = boto3.client("bedrock-agentcore-control", region_name=REGION)
    configs = c.list_online_evaluation_configs().get("onlineEvaluationConfigs", [])
    ours = [cfg for cfg in configs if cfg.get("onlineEvaluationConfigName", "").startswith("agentstudio_ws_")]
    assert ours, (
        "no agentstudio_ws_* OnlineEvaluationConfig found — create a workspace "
        "through the frontend to trigger provisioning, then re-run this test"
    )
    assert any(cfg.get("status") in ("ACTIVE", "CREATING", "UPDATING") for cfg in ours), (
        f"eval configs exist but none are healthy: {[(cfg.get('onlineEvaluationConfigName'), cfg.get('status')) for cfg in ours]}"
    )


def test_eval_execution_role_is_assumable_by_agentcore():
    """The evaluator role must trust bedrock-agentcore.amazonaws.com,
    scoped to this account. A trust-policy regression here kills all
    future eval config creations."""
    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=f"AgentStudioEvaluatorExecution-{REGION}")["Role"]
    doc = role["AssumeRolePolicyDocument"]
    # IAM returns either a dict or URL-encoded JSON depending on client
    if isinstance(doc, str):
        import urllib.parse

        doc = json.loads(urllib.parse.unquote(doc))
    principals = [
        s["Principal"]["Service"] for s in doc["Statement"] if s.get("Principal", {}).get("Service")
    ]
    assert "bedrock-agentcore.amazonaws.com" in principals, f"unexpected trust policy: {doc}"


def test_spans_log_group_exists():
    """Sprint 2 F4 requires the aws/spans log group (OTEL destination)."""
    c = boto3.client("logs", region_name=REGION)
    groups = c.describe_log_groups(logGroupNamePrefix="aws/spans").get("logGroups", [])
    assert any(g["logGroupName"] == "aws/spans" for g in groups), (
        "aws/spans log group missing — run: aws xray update-trace-segment-destination --destination CloudWatchLogs"
    )


def test_meta_agent_runtime_reachable():
    """Meta-Agent must be live (the synthetic AgentCard endpoint reads
    its metadata via GetAgentRuntime). Plan's Task 7 A2A migration was
    downgraded to a fallback — we keep it on HTTP to preserve chat."""
    meta_id = os.environ.get("AGENT_STUDIO_META_AGENT_ID") or os.environ.get(
        "AGENT_STUDIO_EXISTING_META_AGENT_ID"
    )
    assert meta_id, "AGENT_STUDIO_META_AGENT_ID env required"
    c = boto3.client("bedrock-agentcore-control", region_name=REGION)
    info = c.get_agent_runtime(agentRuntimeId=meta_id)
    assert info.get("status") == "READY", f"Meta-Agent not READY: status={info.get('status')}"
    # Fallback stamp: we are deliberately on HTTP (not A2A). If someone
    # migrates later, this assertion and the synthetic-card path in
    # lambda/crud/meta_agent.py both need updating.
    proto = (info.get("protocolConfiguration") or {}).get("serverProtocol")
    assert proto in (None, "HTTP"), (
        f"Meta-Agent protocol changed to {proto!r}; the synthetic AgentCard "
        "endpoint was designed for the HTTP fallback"
    )


# ---------------------------------------------------------------------------
# Sprint 3 — A2A proxy live guards
# ---------------------------------------------------------------------------


def test_sprint3_a2a_public_card_reachable():
    """Every healthy agent should serve a spec-compliant public card."""
    import urllib.request

    cf = os.environ.get("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "")
    assert cf, "AGENT_STUDIO_CLOUDFRONT_DOMAIN required"
    url = f"https://{cf}/a2a/agents/{SUBAGENT_ID}/.well-known/agent-card.json"
    with urllib.request.urlopen(url, timeout=10) as resp:
        assert resp.status == 200
        card = json.load(resp)
    assert card["name"], card
    assert card["protocolVersion"] == "0.3.0", card
    assert card["securitySchemes"]["bearerAuth"]["scheme"] == "bearer", card
    assert card["url"].endswith(f"/a2a/agents/{SUBAGENT_ID}"), card


def test_sprint3_meta_agent_card_reachable():
    """Meta-Agent serves its card at /a2a/meta-agent/.well-known/..."""
    import urllib.request

    cf = os.environ["AGENT_STUDIO_CLOUDFRONT_DOMAIN"]
    with urllib.request.urlopen(
        f"https://{cf}/a2a/meta-agent/.well-known/agent-card.json", timeout=10
    ) as resp:
        card = json.load(resp)
    assert card["name"], card
    assert card["url"].endswith("/a2a/meta-agent"), card


def test_sprint3_extended_card_requires_bearer():
    """Without a Bearer token, extended card returns 401 + WWW-Authenticate."""
    import urllib.error
    import urllib.request

    cf = os.environ["AGENT_STUDIO_CLOUDFRONT_DOMAIN"]
    url = f"https://{cf}/a2a/agents/{SUBAGENT_ID}/authenticatedExtendedCard"
    try:
        urllib.request.urlopen(url, timeout=10)
        raise AssertionError("Expected 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401
        # CloudFront may rewrite the header name; accept either form.
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        auth_hdr = hdrs.get("www-authenticate") or hdrs.get("x-amzn-remapped-www-authenticate", "")
        assert "Bearer" in auth_hdr, dict(e.headers)


def test_sprint3_a2a_keys_table_exists():
    """DDB table for API keys must be ACTIVE with the user-agent-index GSI."""
    ddb = boto3.client("dynamodb", region_name=REGION)
    info = ddb.describe_table(TableName="agent-studio-a2a-keys")
    assert info["Table"]["TableStatus"] == "ACTIVE"
    gsis = {g["IndexName"] for g in info["Table"].get("GlobalSecondaryIndexes", [])}
    assert "user-agent-index" in gsis, gsis
