"""get_agent_detail — Get compact configuration of a deployed agent.

The raw ``metadata.json`` for an agent can balloon into the tens of kilobytes
once many skills are attached (each skill carries its full file list) and
``tool_definitions`` holds the complete Python source for every ``@tool``
function. When the Meta-Agent fans out ``get_agent_detail`` across several
agents in parallel, the combined tool-result payload has been observed to
stall the upstream LLM stream.

This tool therefore returns a slimmed view that preserves every field the
Meta-Agent actually reasons about (id, name, status, model, system prompt,
skill identities, tool identities + signatures) while dropping or collapsing
the bulk contents:

* ``skills[].files`` — a ``list[str]`` of per-skill file paths (hundreds of
  entries for big skills like ``ppt-generator``) collapses to
  ``file_count`` + ``files_preview`` (up to 3 sample names).
* ``tool_definitions`` — full Python source for each ``@tool`` parses via
  ``ast`` into ``{name, signature, summary}`` entries. On a ``SyntaxError``
  we return a truncated 2 KB head plus ``_parse_error`` instead of the full
  source — so a broken definition can't reintroduce the bloat that made us
  slim it in the first place.
* ``system_prompt`` — left as-is (typically under 5 KB).

Callers that need the untouched bytes (e.g. ``preview_assembled_code``,
``sync_agent_skill``) still read ``metadata.json`` directly from S3; this
tool is only a read-only summary for the Meta-Agent's conversation surface.
"""

import ast
import json
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ensure_agent_in_workspace, ROLE_VIEWER


_FILES_PREVIEW_COUNT = 3
_TOOL_DEFS_TRUNCATE_BYTES = 2000


def _iso_utc(value) -> str:
    """Serialize a datetime as a UTC ISO-8601 string so downstream
    consumers parse it as UTC instead of silently treating it as local.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _decorator_name(node: ast.expr) -> str:
    """Best-effort ``@decorator`` name extraction.

    Matches ``@tool``, ``@tool()``, ``@strands.tool``, ``@mod.tool()``. We
    only need the trailing identifier to decide whether the function is a
    Strands ``@tool`` — we don't care about the module path.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _is_tool_decorated(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(d) == "tool" for d in func.decorator_list)


def _summarize_docstring(doc: str | None) -> str:
    """First non-empty line of the docstring, trimmed."""
    if not doc:
        return ""
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _compact_tool_definitions(src: str) -> dict | list:
    """Parse tool_definitions source into a compact list of signatures.

    Returns ``list[{name, signature, summary}]`` on success. On parse
    failure returns ``{"_parse_error": ..., "raw_truncated": ...,
    "raw_size_bytes": ...}`` — NOT the full source, which would defeat
    the whole point of slimming.
    """
    if not src or not src.strip():
        return []

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {
            "_parse_error": f"{e.msg} (line {e.lineno})",
            "raw_truncated": src[:_TOOL_DEFS_TRUNCATE_BYTES],
            "raw_size_bytes": len(src),
        }

    out: list[dict] = []
    # Walking tree.body (not ast.walk) skips nested functions naturally —
    # Strands @tool functions are always module-level.
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_tool_decorated(node):
            continue
        try:
            args_str = ast.unparse(node.args)
            returns_str = ast.unparse(node.returns) if node.returns else "Any"
            signature = f"{node.name}({args_str}) -> {returns_str}"
        except Exception:
            signature = f"{node.name}(...)"
        out.append({
            "name": node.name,
            "signature": signature,
            "summary": _summarize_docstring(ast.get_docstring(node)),
        })
    return out


def _slim_skill(skill: dict) -> dict:
    """Collapse ``skill['files']`` (list of path strings) into a count +
    short preview while keeping every other skill field intact.
    """
    if not isinstance(skill, dict):
        return skill
    slimmed = {k: v for k, v in skill.items() if k != "files"}
    raw_files = skill.get("files")
    if isinstance(raw_files, list):
        slimmed["file_count"] = len(raw_files)
        # Take only the first N path names; they're already sorted by
        # upstream writers (attach_agent_skill / sync_agent_skill), so the
        # preview is deterministic.
        slimmed["files_preview"] = [str(p) for p in raw_files[:_FILES_PREVIEW_COUNT]]
    return slimmed


def _slim_metadata(metadata: dict) -> dict:
    """Apply per-field slimming. Non-targeted fields pass through."""
    if not isinstance(metadata, dict):
        return metadata
    slimmed = dict(metadata)

    skills = metadata.get("skills")
    if isinstance(skills, list):
        slimmed["skills"] = [_slim_skill(s) for s in skills]

    if "tool_definitions" in metadata:
        slimmed["tool_definitions"] = _compact_tool_definitions(
            metadata.get("tool_definitions") or ""
        )

    return slimmed


@tool
def get_agent_detail(agent_id: str) -> str:
    """Get compact configuration of a deployed agent: runtime status, model, system prompt, skill identities, and tool signatures.

    Returns a slimmed view — not the raw S3 metadata.json. Bulky fields
    collapse: skill file lists become ``file_count`` + ``files_preview``,
    and ``tool_definitions`` parses into ``{name, signature, summary}``
    per @tool function. ``system_prompt`` is preserved verbatim.

    Args:
        agent_id: The agent runtime ID (e.g., "myAgent-abc123").

    Returns:
        JSON with the slimmed agent configuration.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_VIEWER)
    if err:
        return json.dumps(err)

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    try:
        runtime = control.get_agent_runtime(agentRuntimeId=agent_id)
    except Exception as e:
        return json.dumps({"error": f"Agent runtime lookup failed: {e}"})

    agent_name = runtime["agentRuntimeName"]
    # Summary fields first so that if a downstream consumer truncates the
    # payload mid-stream (the exact failure mode that motivated this
    # slimming), the header survives.
    result = {
        "agent_id": agent_id,
        "name": agent_name,
        "status": runtime["status"],
        "arn": runtime["agentRuntimeArn"],
        "created_at": _iso_utc(runtime.get("createdAt")),
        "updated_at": _iso_utc(runtime.get("lastUpdatedAt")),
        "description": runtime.get("description", ""),
    }

    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        metadata = json.loads(obj["Body"].read().decode("utf-8"))
        result["metadata"] = _slim_metadata(metadata)
    except Exception:
        result["metadata"] = None
        result["metadata_note"] = "No metadata.json found (agent may have been created before metadata support)"

    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
