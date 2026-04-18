"""End-to-end smoke test for Sprint 1 shared sandbox (Code Interpreter + Browser).

This test exercises the *account-shared* AgentCore resources provisioned
by infra/lib/constructs/agentcore-shared.ts — not the sub-agent code
path. It proves:

  1. The CDK-managed CodeInterpreter is reachable and can execute Python.
  2. The CDK-managed Browser is reachable and surfaces a CDP endpoint.
  3. Our runtime IAM (the caller's role — ops / CI) can list/create/stop
     sessions on these resources.

What it does NOT cover: sub-agent-process tool wiring. Sub-agent end-to-
end exercise requires deploying a minimal sub-agent through meta-agent,
which is expensive and flaky. That remains a manual smoke step in the
deploy runbook.

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
