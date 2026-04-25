"""Meta-Agent metadata endpoints.

The Meta-Agent currently runs with protocolConfiguration.serverProtocol=HTTP
(the frontend chat depends on that HTTP+SSE wire format). A2A's
GetAgentCard data plane API therefore rejects with ValidationException.

To still give users the A2A discoverability story (and "copy endpoint"
UX), this endpoint *synthesizes* an A2A-compatible AgentCard from the
runtime metadata (GetAgentRuntime) + hard-coded capability description
of the Meta-Agent itself. The shape matches a2a.types.AgentCard so any
external A2A client can consume it.
"""
from urllib.parse import quote

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import META_AGENT_ARN, REGION
from shared.middleware import auth_check
from shared.response import success, bad_request, internal_error

router = Router()
logger = Logger(child=True)

_control = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _extract_runtime_id(arn: str) -> str:
    # arn:aws:bedrock-agentcore:<region>:<acct>:runtime/<id>
    parts = arn.rsplit("/", 1)
    return parts[-1] if len(parts) == 2 else ""


def _build_invocation_url(arn: str, region: str) -> str:
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations"
    )


# Skills are hard-coded: the Meta-Agent's capabilities are known at build
# time and don't come from runtime metadata. Keep the list short and
# accurate — external A2A clients use this to decide whether to route to
# us.
_META_AGENT_SKILLS = [
    {
        "id": "agent_lifecycle",
        "name": "Manage sub-agents",
        "description": "Create, update, validate, deploy, and delete sub-agents.",
        "tags": ["agent", "create", "update", "delete", "deploy"],
    },
    {
        "id": "skill_management",
        "name": "Manage skills",
        "description": "Create, import, list, and modify reusable skills (SKILL.md format).",
        "tags": ["skill", "markdown", "prompt"],
    },
    {
        "id": "debugging",
        "name": "Debug sub-agents",
        "description": "Inspect logs, run validation, and analyze traces for deployed agents.",
        "tags": ["logs", "trace", "debug"],
    },
]


@router.get("/api/workspaces/<wsId>/meta-agent/status")
def get_meta_agent_status(wsId: str):
    """Lightweight health probe for the Meta-Agent runtime.

    Powers the status dot in the chat header. Only returns the live
    control-plane status + a timestamp; any richer metadata lives in
    the agent-card endpoint.
    """
    _, _, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    if not META_AGENT_ARN:
        return success({"status": "NOT_CONFIGURED", "lastUpdated": None})

    runtime_id = _extract_runtime_id(META_AGENT_ARN)
    try:
        info = _get_control().get_agent_runtime(agentRuntimeId=runtime_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return success({"status": "NOT_FOUND", "lastUpdated": None})
        logger.exception("get_agent_runtime failed", extra={"error_code": code})
        return internal_error()

    last_updated = info.get("lastUpdatedAt") or info.get("createdAt")
    return success({
        "status": info.get("status") or "UNKNOWN",
        "lastUpdated": last_updated.isoformat() if last_updated else None,
    })


@router.get("/api/workspaces/<wsId>/meta-agent/agent-card")
def get_meta_agent_card(wsId: str):
    _, _, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    if not META_AGENT_ARN:
        return bad_request("META_AGENT_ARN not configured")

    runtime_id = _extract_runtime_id(META_AGENT_ARN)
    try:
        info = _get_control().get_agent_runtime(agentRuntimeId=runtime_id)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("get_agent_runtime failed", extra={"error_code": code})
        return internal_error()

    name = info.get("agentRuntimeName", "agentStudioMeta")
    version = str(info.get("agentRuntimeVersion", "1"))
    url = _build_invocation_url(META_AGENT_ARN, REGION)

    card = {
        "name": name,
        "description": "Agent Studio Meta-Agent — orchestrates sub-agent lifecycle.",
        "url": url,
        "version": version,
        "protocolVersion": "0.3.0",
        "preferredTransport": "HTTP",
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "capabilities": {"streaming": True},
        "skills": _META_AGENT_SKILLS,
        # Extension fields useful for the UI / operators.
        "runtimeArn": META_AGENT_ARN,
        "runtimeStatus": info.get("status"),
    }
    return success(card)
