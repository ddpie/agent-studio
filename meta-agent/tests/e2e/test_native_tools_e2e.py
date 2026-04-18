"""End-to-end smoke test for Sprint 1 shared sandbox (Code Interpreter + Browser).

This test exercises the *account-shared* AgentCore resources provisioned
by infra/lib/constructs/agentcore-shared.ts — not the sub-agent code
path. It proves:

  1. The CDK-managed CodeInterpreter is reachable and can execute Python.
  2. The CDK-managed Browser is reachable and surfaces a CDP endpoint.
  3. Our runtime IAM (the caller's role — ops / CI) can list/create/stop
     sessions on these resources.

It also runs two sub-agent-process tests against an existing deployed
sub-agent (default: `claudewatch-kGlipG6kpy`, override via
AGENT_STUDIO_E2E_SUBAGENT_ID). These are the tests that actually prove
`run_command` / `fetch_webpage` route through the shared sandbox: we
invoke the sub-agent with a prompt, wait for the reply, then check
that a fresh CI / Browser session appeared on the shared resource
within the last 10 minutes.

Prerequisite: the sub-agent must be deployed against the current
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
    return any(
        (now - it["createdAt"]).total_seconds() < age_s
        for it in items
        if it.get("createdAt")
    )


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
    assert streams["automationStream"]["streamEndpoint"].startswith("wss://"), \
        f"CDP endpoint should be wss://, got {streams['automationStream']['streamEndpoint']!r}"

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
    """Invoke sub-agent runtime; retry on cold-start timeout."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    last_err = None
    for attempt in range(max_attempts):
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
    raise AssertionError(f"sub-agent {agent_id} cold-start never recovered: {last_err}")


def test_subagent_fetch_webpage_routes_through_browser(agentcore):
    """Invoke a deployed sub-agent with a fetch_webpage prompt and confirm
    a Browser session was created on the shared resource.

    Prerequisite: the chosen sub-agent's tools.py must use the
    tools_library/fetch_webpage.py variant (Browser-backed), not a
    user-authored custom fetch_webpage (urllib-backed). Skips if the
    current deployment has a custom version — we detect by sniffing the
    deployed tools.py for the Browser call pattern.
    """
    import io, zipfile
    s3 = boto3.client("s3", region_name=REGION)
    bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
    zip_bytes = s3.get_object(Bucket=bucket, Key=f"agents/{SUBAGENT_ID}/deployment.zip")["Body"].read()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        tools_py = z.read("tools.py").decode("utf-8", errors="ignore")
    if "start_browser_session" not in tools_py:
        pytest.skip(
            f"sub-agent {SUBAGENT_ID} tools.py does not use Browser-backed "
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
        f"sub-agent did not successfully fetch example.com; "
        f"tail of reply: {reply[-400:]}"
    )

    listed = agentcore.list_browser_sessions(browserIdentifier=BR_ID).get("items", [])
    new_recent = [
        s for s in listed
        if s["sessionId"] not in recent_before and _find_recent([s], age_s=120)
    ]
    assert new_recent, (
        f"expected a new Browser session in the last 2 min on {BR_ID}, "
        f"got {len(listed)} total sessions; baseline_recent={len(recent_before)}"
    )


def test_subagent_run_command_routes_through_code_interpreter(agentcore):
    """Invoke a deployed sub-agent with a run_command prompt and confirm
    a Code Interpreter session was created on the shared resource."""
    baseline = agentcore.list_code_interpreter_sessions(codeInterpreterIdentifier=CI_ID).get("items", [])
    recent_before = {s["sessionId"] for s in baseline if _find_recent([s], age_s=60)}

    reply = _invoke_subagent(
        SUBAGENT_ID,
        "Use run_command with language='python' and code 'print(6*7)'. Return only the numeric stdout.",
    )
    assert "42" in reply, f"expected 42 in reply tail: {reply[-400:]}"

    listed = agentcore.list_code_interpreter_sessions(codeInterpreterIdentifier=CI_ID).get("items", [])
    new_recent = [
        s for s in listed
        if s["sessionId"] not in recent_before and _find_recent([s], age_s=120)
    ]
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
        f"BROWSER_ID {BR_ID} is not a CFN output of AgentStudioStack "
        f"(outputs: {br_keys})"
    )
