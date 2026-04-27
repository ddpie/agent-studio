"""list_mcp_servers — List MCP servers enabled in the current workspace.

v4 per-workspace model (spec §7.2): data source is registry YAML + the
workspace's `mcp_runtimes` map (DDB workspaces META). No Gateway calls.
"""
import json
import os
from typing import Any

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import current_workspace


_WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")


def _s3():
    return boto3.client("s3", region_name=REGION)


def _ws_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(_WORKSPACES_TABLE)


def _load_registry() -> dict:
    try:
        resp = _s3().get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        import yaml
        return yaml.safe_load(resp["Body"].read())
    except Exception:
        return {"runtime_targets": [], "remote_targets": []}


def _get_ws_meta(ws_id: str) -> dict | None:
    return _ws_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
    ).get("Item")


@tool
def list_mcp_servers() -> str:
    """List MCP servers available in the current workspace.

    Shows registry targets + per-workspace enablement status. Agents can only
    reference MCP targets whose ``runtime.status == "READY"``. For targets not
    yet enabled, tell the user to enable them via ``enable_mcp(target)`` or
    the /#/mcp page.

    Returns:
        JSON with ``workspaceRoleExists`` flag, ``categories`` grouped by
        category, and per-target fields: name, description, sensitivity,
        enabled, runtime (status/version/updated_at if enabled).
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "No workspace context."})

    ws_meta = _get_ws_meta(ws_id) or {}
    workspace_role_exists = bool(ws_meta.get("roleArn"))
    mcp_runtimes = ws_meta.get("mcp_runtimes") or {}

    registry = _load_registry()
    targets_by_cat: dict[str, list] = {}

    for t in (registry.get("runtime_targets") or []):
        if not t.get("enabled", True):
            continue
        if t.get("vpc_required"):
            continue
        name = t["name"]
        runtime_entry = mcp_runtimes.get(name)
        runtime_summary = None
        if runtime_entry:
            runtime_summary = {
                "status": runtime_entry.get("status"),
                "image_version": runtime_entry.get("image_version"),
                "last_error": runtime_entry.get("last_error"),
                "updated_at": runtime_entry.get("updated_at"),
            }
        cat = t.get("category", "general")
        targets_by_cat.setdefault(cat, []).append({
            "name": name,
            "description": t.get("description", ""),
            "sensitivity": t.get("sensitivity", "low"),
            "enabled": runtime_entry is not None,
            "runtime": runtime_summary,
            "latest_version": t.get("version") or "latest",
        })

    total = sum(len(v) for v in targets_by_cat.values())
    enabled = sum(1 for v in targets_by_cat.values() for t in v if t["enabled"])
    ready = sum(
        1 for v in targets_by_cat.values() for t in v
        if t["enabled"] and (t.get("runtime") or {}).get("status") in ("READY", "ACTIVE")
    )

    return json.dumps({
        "workspaceRoleExists": workspace_role_exists,
        "total": total,
        "enabled": enabled,
        "ready": ready,
        "categories": {cat: lst for cat, lst in sorted(targets_by_cat.items())},
    }, ensure_ascii=False, default=str)
