"""link_agent / unlink_agent — wire a source agent up to call a target peer via A2A.

Linking a agent A to peer B does the following, atomically from the
caller's perspective:

  1. Validates both agents exist in the same workspace and the caller has
     at least editor-level membership in that workspace (same bar as
     update_agent in the CRUD Lambda). Membership is looked up in the
     `agent-studio-workspaces` table at `{workspaceId, sk=MEMBER#<user>}`,
     matching the shape used by `lambda/shared/auth.py`.
  2. Mints a fresh A2A API key for `(caller_id, target_id)`. Hashing and
     DDB shape mirror `lambda/crud/a2a_keys.py::create_key` 1:1 so the
     deployed a2a-proxy Lambda validates it without any code change there.
  3. Stores the plaintext key in source-agent secrets under the
     `AGENTS_TOOL_KEYS_JSON` bucket (JSON map of {targetId: apiKey}).
  4. Adds `call_agent` to the source's `tool_names` (idempotent), appends
     the `agent_caller` tool-library code to `tool_definitions`, and
     appends a short prompt fragment naming the linked agent.
  5. Redeploys the source agent runtime with the merged env vars so
     `call_agent` can read `A2A_INVOKE_URL` + `AGENTS_TOOL_KEYS_JSON`.

`unlink_agent` reverses 2-5 for a specific target (revokes the key, strips
the map entry, removes the prompt fragment, optionally removes call_agent
if no peers remain).

This all runs out-of-band of the HTTP CRUD path — no new Lambdas required
and the A2A proxy / AgentCore Runtime already enforce their own auth.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from config import AGENTS_TABLE, MODEL_ID, REGION, S3_BUCKET, SUB_AGENT_ROLE_ARN
from deploy import (
    _shared_env_vars,
    build_deployment_package_v2,
    upload_deployment,
    validate_agent_files,
    wait_for_ready,
)
from strands import tool

from templates.agent_template_v2 import MAIN_PY_MCP_TEMPLATE, MAIN_PY_TEMPLATE, TOOLS_PY_HEADER
from tools_library.registry import get_tool_code_by_func_name as _get_builtin_code

# Mirrors lambda/crud/a2a_keys.py
_A2A_KEYS_TABLE = os.getenv("AGENT_STUDIO_A2A_KEYS_TABLE", "agent-studio-a2a-keys")
_KEY_CHARSET = "ABCDEFGHJKMNPQRSTVWXYZabcdefghijkmnpqrstuvwxyz23456789"
_A2A_INVOKE_URL_ENV_KEY = "A2A_INVOKE_URL"
_A2A_KEYS_ENV_KEY = "AGENTS_TOOL_KEYS_JSON"
_LINK_MARKER_START = "<!-- linked-agents:start -->"
_LINK_MARKER_END = "<!-- linked-agents:end -->"

# Role constants come from tools._scope (single source of truth). Fall
# back to local definitions only if _scope isn't importable — that shouldn't
# happen in practice but protects test isolation.
_WORKSPACES_TABLE = os.getenv("AGENT_STUDIO_WORKSPACES_TABLE", "agent-studio-workspaces")
try:
    from tools._scope import ROLE_ADMIN, ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER
    _ROLE_LEVEL = {ROLE_VIEWER: 0, ROLE_EDITOR: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}
except ImportError:  # pragma: no cover
    ROLE_VIEWER, ROLE_EDITOR, ROLE_ADMIN, ROLE_OWNER = "viewer", "editor", "admin", "owner"
    _ROLE_LEVEL = {ROLE_VIEWER: 0, ROLE_EDITOR: 1, ROLE_ADMIN: 2, ROLE_OWNER: 3}


def _get_workspace_membership(workspace_id: str, user_id: str) -> dict | None:
    """Fetch a workspace membership record. Shape matches lambda/shared/auth.py."""
    if not workspace_id or not user_id:
        return None
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        resp = ddb.Table(_WORKSPACES_TABLE).get_item(
            Key={"workspaceId": workspace_id, "sk": f"MEMBER#{user_id}"},
            ConsistentRead=True,
        )
        return resp.get("Item")
    except Exception:
        return None


def _has_min_role(member: dict | None, min_role: str) -> bool:
    if not member:
        return False
    return _ROLE_LEVEL.get(member.get("role", ""), -1) >= _ROLE_LEVEL.get(min_role, 99)


def _public_base_url() -> str:
    """Resolve the CloudFront public base URL for the A2A proxy.

    Order of precedence:
      1. AGENT_STUDIO_CLOUDFRONT_DOMAIN env var (set on meta-agent runtime)
      2. AGENT_STUDIO_A2A_INVOKE_URL env var (explicit override)
      3. "" (caller must handle empty)
    """
    explicit = os.getenv("AGENT_STUDIO_A2A_INVOKE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    cf = os.getenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "").strip()
    if cf:
        if cf.startswith(("http://", "https://")):
            return cf.rstrip("/")
        return f"https://{cf}"
    return ""


def _caller_from_module() -> str:
    return getattr(
        __import__("tools.create_agent", fromlist=["_caller_id"]),
        "_caller_id",
        "unknown",
    )


def _mint_a2a_key(user_id: str, agent_id: str, workspace_id: str) -> tuple[str, str]:
    """Create a fresh A2A key pinned to (user_id, agent_id). Returns (key_id, plaintext).

    DDB schema is identical to lambda/crud/a2a_keys.py::create_key so the
    already-deployed a2a-proxy lambda validates it via the same SHA-256 lookup.
    """
    raw = os.urandom(32)
    n = len(_KEY_CHARSET)
    random_part = "".join(_KEY_CHARSET[b % n] for b in raw)
    plaintext = f"as_{random_part}"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    prefix = plaintext[:8]
    key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_A2A_KEYS_TABLE)
    table.put_item(Item={
        "apiKeyHash": key_hash,
        "keyId": key_id,
        "userAgentKey": f"{user_id}#{agent_id}",
        "keyPrefix": prefix,
        "userId": user_id,
        "agentId": agent_id,
        "workspaceId": workspace_id,
        "createdAt": now,
        "revoked": False,
        "source": "meta-agent.link_agent",
    })
    return key_id, plaintext


def _revoke_a2a_key(key_hash: str) -> None:
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_A2A_KEYS_TABLE)
    try:
        table.update_item(
            Key={"apiKeyHash": key_hash},
            UpdateExpression="SET revoked = :r",
            ExpressionAttributeValues={":r": True},
        )
    except Exception:
        pass


def _linked_keys_secret_path(workspace_id: str, source_agent_id: str) -> str:
    """Per-key Secret path that holds the JSON map of linked A2A keys.

    Shares the ``agent-studio/{ws}/{agent}/{KEY}`` layout used by the UI
    and ``manage_secrets`` so a single IAM policy + a single hydrate path
    covers both user-set secrets and linked-agent keys.
    """
    return f"agent-studio/{workspace_id}/{source_agent_id}/{_A2A_KEYS_ENV_KEY}"


def _read_linked_keys_map(sm, workspace_id: str, source_agent_id: str) -> dict:
    try:
        existing = sm.get_secret_value(
            SecretId=_linked_keys_secret_path(workspace_id, source_agent_id)
        )
    except sm.exceptions.ResourceNotFoundException:
        return {}
    except Exception:
        return {}
    try:
        loaded = json.loads(existing.get("SecretString", "{}"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _put_linked_keys_blob(sm, workspace_id: str, source_agent_id: str, keys_blob: str) -> None:
    secret_name = _linked_keys_secret_path(workspace_id, source_agent_id)
    try:
        sm.put_secret_value(SecretId=secret_name, SecretString=keys_blob)
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(Name=secret_name, SecretString=keys_blob)


def _update_linked_keys_secret(
    source_agent_id: str,
    target_agent_id: str,
    plaintext: str,
    workspace_id: str,
) -> tuple[str, dict]:
    """Merge {target_agent_id: plaintext} into the linked-keys JSON blob.

    Returns (json_blob, updated_map). The JSON blob is surfaced as the
    ``AGENTS_TOOL_KEYS_JSON`` env var on the source runtime (written into
    metadata.extra_env_vars by the caller).
    """
    sm = boto3.client("secretsmanager", region_name=REGION)
    keys_map = _read_linked_keys_map(sm, workspace_id, source_agent_id)
    keys_map[target_agent_id] = plaintext
    keys_blob = json.dumps(keys_map, ensure_ascii=False)
    _put_linked_keys_blob(sm, workspace_id, source_agent_id, keys_blob)
    return keys_blob, keys_map


def _remove_linked_key_from_secret(
    source_agent_id: str,
    target_agent_id: str,
    workspace_id: str,
) -> tuple[str, dict]:
    sm = boto3.client("secretsmanager", region_name=REGION)
    keys_map = _read_linked_keys_map(sm, workspace_id, source_agent_id)
    if not keys_map:
        return "{}", {}
    keys_map.pop(target_agent_id, None)
    keys_blob = json.dumps(keys_map, ensure_ascii=False)
    try:
        _put_linked_keys_blob(sm, workspace_id, source_agent_id, keys_blob)
    except Exception:
        pass
    return keys_blob, keys_map


def _strip_link_section(prompt: str) -> str:
    pattern = re.compile(
        re.escape(_LINK_MARKER_START) + r".*?" + re.escape(_LINK_MARKER_END),
        flags=re.DOTALL,
    )
    return pattern.sub("", prompt).rstrip() + "\n"


def _build_link_section(linked: list[dict]) -> str:
    if not linked:
        return ""
    lines = [
        "",
        _LINK_MARKER_START,
        "## Linked Peer Agents",
        "You can delegate questions to these peer agents via the `call_agent(agent_id, prompt)` tool.",
        "Only call them when the user's request falls squarely inside the peer's described scope:",
        "",
    ]
    for entry in linked:
        desc = entry.get("description") or "(no description provided)"
        lines.append(f"- `{entry['agent_id']}` — **{entry.get('display_name') or entry['agent_id']}**: {desc}")
    lines.append(_LINK_MARKER_END)
    return "\n".join(lines) + "\n"


def _get_agent(agent_id: str) -> dict | None:
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.Table(AGENTS_TABLE).get_item(Key={"agentId": agent_id}).get("Item")


def _load_metadata(agent_id: str) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return {}


def _save_metadata(agent_id: str, meta: dict) -> None:
    s3 = boto3.client("s3", region_name=REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(meta, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    # Mirror system_prompt + tool_definitions to their standalone files.
    # The frontend edit form reads these (NOT metadata.json) when the
    # user opens the agent. If we skip this mirror, clicking "更新"
    # immediately after a link round-trip will re-upload whatever stale
    # tool_definitions.py was last saved — clobbering the fresh
    # call_agent code link_agent just wrote into metadata.json.
    prompt = meta.get("system_prompt") or ""
    tool_defs = meta.get("tool_definitions") or ""
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"agents/{agent_id}/system_prompt.txt",
            Body=prompt.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"agents/{agent_id}/tool_definitions.py",
            Body=tool_defs.encode("utf-8"),
            ContentType="text/x-python; charset=utf-8",
        )
    except Exception:
        # Best-effort — the canonical store is metadata.json.
        pass


def _redeploy_source(source_id: str, meta: dict) -> str:
    """Rebuild + redeploy the source agent runtime with updated tools/prompt/env."""
    tool_names_list = meta.get("tools") or []
    custom_code = meta.get("tool_definitions") or ""
    defined = set(re.findall(r"@tool\s*\ndef\s+(\w+)\s*\(", custom_code)) if custom_code.strip() else set()

    builtin_parts = []
    for name in tool_names_list:
        if name in defined:
            continue
        code = _get_builtin_code(name)
        if code:
            builtin_parts.append(code.strip())

    mcp_endpoints = meta.get("mcp_endpoints") or []
    # Fall back to whatever is in config.json if metadata lacks endpoints
    if not mcp_endpoints and meta.get("mcp_targets"):
        try:
            s3 = boto3.client("s3", region_name=REGION)
            cfg = json.loads(
                s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{source_id}/config.json")["Body"].read().decode("utf-8")
            )
            mcp_endpoints = cfg.get("mcp_endpoints", [])
        except Exception:
            mcp_endpoints = []

    main_py = MAIN_PY_MCP_TEMPLATE if mcp_endpoints else MAIN_PY_TEMPLATE
    tools_py = TOOLS_PY_HEADER + "\n\n".join(
        builtin_parts + ([custom_code] if custom_code.strip() else [])
    )

    # Inject KB retrieval tool if agent has bound knowledge bases
    _ws_id = meta.get("workspace_id", "")
    _kb_ids = []
    if not _kb_ids:
        try:
            _agent_item = boto3.client("dynamodb", region_name=REGION).get_item(
                TableName=AGENTS_TABLE, Key={"agentId": {"S": source_id}},
                ProjectionExpression="knowledge_bases",
            ).get("Item", {})
            _kb_ids = _agent_item.get("knowledge_bases", {}).get("SS", [])
        except Exception:
            pass
    if _kb_ids and _ws_id:
        from tools.kb_inject import build_kb_injection, resolve_kb_bindings
        _kb_records = resolve_kb_bindings(_ws_id, _kb_ids)
        _kb_code = build_kb_injection(_kb_records)
        if _kb_code:
            tools_py = tools_py + "\n\n" + _kb_code
            if "kb_retrieve" not in tool_names_list:
                tool_names_list.append("kb_retrieve")

    prompt_txt = meta.get("system_prompt", "")
    config_data = {
        "model_id": meta.get("model_id") or MODEL_ID,
        "tool_names": tool_names_list,
    }
    if mcp_endpoints:
        config_data["mcp_endpoints"] = mcp_endpoints
    if meta.get("mcp_targets"):
        config_data["mcp_targets"] = meta["mcp_targets"]
    config_json = json.dumps(config_data, indent=2, ensure_ascii=False)

    validation = validate_agent_files(main_py, tools_py, prompt_txt, config_json)
    if not validation["valid"]:
        raise RuntimeError(f"Code validation failed after link: {validation['errors']}")

    package = build_deployment_package_v2(main_py, tools_py, prompt_txt, config_json)
    s3_key = upload_deployment(source_id, package)

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.update_agent_runtime(
        agentRuntimeId=source_id,
        roleArn=SUB_AGENT_ROLE_ARN,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": s3_key}},
                "runtime": "PYTHON_3_10",
                "entryPoint": ["main.py"],
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        filesystemConfigurations=[{"sessionStorage": {"mountPath": "/mnt/workspace"}}],
        environmentVariables=_shared_env_vars(agent_id=source_id),
    )
    return wait_for_ready(source_id)


@tool
def link_agent(source_agent_id: str, target_agent_id: str) -> str:
    """Link a source agent so it can call another agent as a tool via A2A.

    After linking, the source agent gains a `call_agent(agent_id, prompt)` tool
    and its system prompt learns that the target exists. Both agents must live
    in the same workspace; cross-workspace linking is rejected in this iteration.
    The caller must have at least `editor` role in that workspace — viewers and
    non-members are rejected because linking mutates secrets and redeploys the
    source runtime.

    The source agent is redeployed in place — runtime id/ARN stay the same.

    Args:
        source_agent_id: The agent that should gain the ability to call another agent.
        target_agent_id: The agent that the source will delegate to.

    Returns:
        JSON describing what changed, or an error.
    """
    if source_agent_id == target_agent_id:
        return json.dumps({"error": "Cannot link an agent to itself"})

    caller = _caller_from_module()
    src = _get_agent(source_agent_id)
    tgt = _get_agent(target_agent_id)
    if not src:
        return json.dumps({"error": f"Source agent {source_agent_id} not found"})
    if not tgt:
        return json.dumps({"error": f"Target agent {target_agent_id} not found"})

    src_ws = src.get("workspace_id", "")
    tgt_ws = tgt.get("workspace_id", "")
    if not src_ws or not tgt_ws:
        return json.dumps({"error": "Both agents must belong to a workspace"})
    if src_ws != tgt_ws:
        return json.dumps({
            "error": "Cross-workspace linking is not allowed",
            "source_workspace": src_ws,
            "target_workspace": tgt_ws,
        })

    # Editor+ role required: linking mutates secrets and redeploys source.
    member = _get_workspace_membership(src_ws, caller)
    if not _has_min_role(member, ROLE_EDITOR):
        return json.dumps({
            "error": "Permission denied: editor role or higher required to link agents",
            "workspace_id": src_ws,
            "caller_role": (member or {}).get("role", "none"),
        })

    invoke_base = _public_base_url()
    if not invoke_base:
        return json.dumps({"error": "A2A invoke base URL not configured (AGENT_STUDIO_CLOUDFRONT_DOMAIN missing)"})

    # 1. Mint the key.
    key_id, plaintext = _mint_a2a_key(
        user_id=caller,
        agent_id=target_agent_id,
        workspace_id=tgt_ws,
    )

    # 2. Merge into source's secrets. Keyed by the source agent's workspace
    #    (which is the same as src_ws by construction since cross-workspace
    #    linking was rejected above).
    keys_blob, keys_map = _update_linked_keys_secret(
        source_agent_id, target_agent_id, plaintext, src_ws,
    )

    # 3. Update source metadata — tools, prompt fragment, linked_agents list, env vars.
    meta = _load_metadata(source_agent_id)
    tools_list = list(meta.get("tools") or [])
    if "call_agent" not in tools_list:
        tools_list.append("call_agent")
    meta["tools"] = tools_list
    meta["tool_names"] = ",".join(tools_list)

    # Refresh tool_definitions so the @tool code matches the CURRENT
    # tools_library template (not whatever was frozen on first link). Older
    # agents had a call_agent body that used message/send + no
    # x-amz-content-sha256 header, which now trips CloudFront OAC's SigV4
    # check and returns HTTP 403 on every A2A call. Re-linking must replace
    # that stale code, not just skip because "def call_agent" is present.
    tool_defs = meta.get("tool_definitions") or ""
    call_code = _get_builtin_code("call_agent") or ""
    if call_code:
        if "def call_agent" in tool_defs:
            # Strip existing @tool/def call_agent block (including its
            # preceding @tool decorator) and re-append the fresh template.
            tool_defs = re.sub(
                r"(?:@tool\s*\n)?def\s+call_agent\s*\([^)]*\)\s*(?:->[^:]+)?:\s*(?:\"\"\"[\s\S]*?\"\"\"\s*)?(?:(?!@tool\b)[\s\S])*?(?=\n@tool\b|\Z)",
                "",
                tool_defs,
                count=1,
            ).rstrip()
        tool_defs = (tool_defs.rstrip() + "\n\n" + call_code.strip()) if tool_defs.strip() else call_code.strip()
        meta["tool_definitions"] = tool_defs

    linked = meta.get("linked_agents") or []
    linked = [l for l in linked if l.get("agent_id") != target_agent_id]
    linked.append({
        "agent_id": target_agent_id,
        "display_name": tgt.get("display_name") or tgt.get("name") or target_agent_id,
        "description": tgt.get("description") or "",
        "key_id": key_id,
        "linked_at": datetime.now(timezone.utc).isoformat(),
    })
    meta["linked_agents"] = linked

    base_prompt = _strip_link_section(meta.get("system_prompt") or "")
    meta["system_prompt"] = base_prompt.rstrip() + "\n" + _build_link_section(linked)

    extra = dict(meta.get("extra_env_vars") or {})
    extra[_A2A_INVOKE_URL_ENV_KEY] = invoke_base
    extra[_A2A_KEYS_ENV_KEY] = keys_blob
    meta["extra_env_vars"] = extra
    meta["workspace_id"] = src_ws
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Persist metadata first so a concurrent update_agent won't clobber us.
    _save_metadata(source_agent_id, meta)

    # 4. Redeploy the source runtime.
    try:
        status = _redeploy_source(source_agent_id, meta)
    except Exception as e:
        return json.dumps({"error": f"Redeploy failed: {e}", "key_id": key_id})

    # 5. Sync DDB tool_names + linked_agents for the UI. The frontend
    # LinkedAgentsSection reads linked_agents off the DDB item (via the
    # GET /agents/{id} endpoint); without this write the UI keeps
    # showing "尚未关联" even after the tool succeeded end-to-end.
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _ddb_linked = [
        {
            "agent_id": l["agent_id"],
            "display_name": l.get("display_name", ""),
            "description": l.get("description", ""),
        }
        for l in linked
    ]
    try:
        ddb.Table(AGENTS_TABLE).update_item(
            Key={"agentId": source_agent_id},
            UpdateExpression="SET tool_names = :tn, linked_agents = :la, updated_at = :ua",
            ExpressionAttributeValues={
                ":tn": tools_list,
                ":la": _ddb_linked,
                ":ua": meta["updated_at"],
            },
        )
    except Exception:
        pass

    return json.dumps({
        "status": status,
        "source_agent_id": source_agent_id,
        "target_agent_id": target_agent_id,
        "workspace_id": src_ws,
        "key_id": key_id,
        "linked_agents": [l["agent_id"] for l in linked],
    }, indent=2)


@tool
def unlink_agent(source_agent_id: str, target_agent_id: str) -> str:
    """Reverse link_agent: revoke the key, drop the prompt fragment + secret entry, redeploy source.

    The `call_agent` tool is removed from the source only when no linked peers remain.
    The caller must have at least `editor` role in the source agent's workspace;
    viewers and non-members are rejected because unlinking mutates secrets and
    redeploys the source runtime.

    Args:
        source_agent_id: The agent that currently has the link.
        target_agent_id: The peer to disconnect.

    Returns:
        JSON with remaining linked agents or an error.
    """
    caller = _caller_from_module()
    src = _get_agent(source_agent_id)
    if not src:
        return json.dumps({"error": f"Source agent {source_agent_id} not found"})

    src_ws = src.get("workspace_id", "")
    if not src_ws:
        return json.dumps({"error": "Source agent has no workspace"})

    member = _get_workspace_membership(src_ws, caller)
    if not _has_min_role(member, ROLE_EDITOR):
        return json.dumps({
            "error": "Permission denied: editor role or higher required to unlink agents",
            "workspace_id": src_ws,
            "caller_role": (member or {}).get("role", "none"),
        })

    meta = _load_metadata(source_agent_id)
    linked = [l for l in (meta.get("linked_agents") or []) if l.get("agent_id") == target_agent_id]
    if not linked:
        return json.dumps({"error": f"Agent {target_agent_id} is not linked to {source_agent_id}"})

    # Revoke the key (best effort — find via the userAgentKey GSI scan is not
    # in scope; if the key was minted by link_agent we know its hash only
    # indirectly, so we fall back to wiping it from the secret bundle which
    # makes the key effectively unusable to the source).
    # If the link entry recorded an apiKeyHash we'd revoke it here.
    remaining_blob, remaining_map = _remove_linked_key_from_secret(
        source_agent_id, target_agent_id, src_ws,
    )

    remaining_linked = [l for l in (meta.get("linked_agents") or []) if l.get("agent_id") != target_agent_id]
    meta["linked_agents"] = remaining_linked

    tools_list = list(meta.get("tools") or [])
    if not remaining_linked and "call_agent" in tools_list:
        tools_list.remove("call_agent")
    meta["tools"] = tools_list
    meta["tool_names"] = ",".join(tools_list)

    if not remaining_linked and meta.get("tool_definitions"):
        # Strip the call_agent @tool block; leave the rest untouched.
        defs = meta["tool_definitions"]
        defs = re.sub(
            r"@tool\s*\ndef\s+call_agent\s*\([^)]*\)\s*(->[^:]+)?:\s*(?:\"\"\".*?\"\"\"\s*)?(?:(?! @tool)[\s\S])*?(?=\n@tool|\Z)",
            "",
            defs,
            flags=re.DOTALL,
        ).strip()
        meta["tool_definitions"] = defs

    base_prompt = _strip_link_section(meta.get("system_prompt") or "")
    if remaining_linked:
        meta["system_prompt"] = base_prompt.rstrip() + "\n" + _build_link_section(remaining_linked)
    else:
        meta["system_prompt"] = base_prompt

    extra = dict(meta.get("extra_env_vars") or {})
    if remaining_map:
        extra[_A2A_KEYS_ENV_KEY] = remaining_blob
    else:
        extra.pop(_A2A_KEYS_ENV_KEY, None)
        extra.pop(_A2A_INVOKE_URL_ENV_KEY, None)
    meta["extra_env_vars"] = extra
    meta["workspace_id"] = src_ws
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_metadata(source_agent_id, meta)

    try:
        status = _redeploy_source(source_agent_id, meta)
    except Exception as e:
        return json.dumps({"error": f"Redeploy failed: {e}"})

    ddb = boto3.resource("dynamodb", region_name=REGION)
    _ddb_remaining = [
        {
            "agent_id": l["agent_id"],
            "display_name": l.get("display_name", ""),
            "description": l.get("description", ""),
        }
        for l in remaining_linked
    ]
    try:
        ddb.Table(AGENTS_TABLE).update_item(
            Key={"agentId": source_agent_id},
            UpdateExpression="SET tool_names = :tn, linked_agents = :la, updated_at = :ua",
            ExpressionAttributeValues={
                ":tn": tools_list,
                ":la": _ddb_remaining,
                ":ua": meta["updated_at"],
            },
        )
    except Exception:
        pass

    return json.dumps({
        "status": status,
        "source_agent_id": source_agent_id,
        "target_agent_id": target_agent_id,
        "remaining_linked_agents": [l["agent_id"] for l in remaining_linked],
    }, indent=2)
