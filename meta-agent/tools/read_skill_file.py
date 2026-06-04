"""read_skill_file / list_skill_files — on-demand skill file access for the
edit-assistant. The sidebar assistant can't afford to inline hundreds of
files up front (ppt-generator alone has 785), so expose a two-step
"list → read" protocol so the model pulls only what it needs.
"""

import json
from typing import Any

import boto3
from config import REGION, S3_BUCKET
from strands import tool

from tools._scope import ROLE_VIEWER, current_workspace, require_role

_SKILLS_TABLE = "agent-studio-skills"
# Cap per read to keep a single file from blowing the Meta-Agent prompt.
# Model output tokens are cheap to retry on a truncated file, but a 500KB
# log file or minified asset would wreck context.
_MAX_FILE_BYTES = 200_000


def _skill_in_workspace(skill_id: str) -> bool:
    """True if the skill's DDB record's workspace_id matches the caller's."""
    if not skill_id:
        return False
    ws = current_workspace()
    if not ws:
        return False
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        item = ddb.Table(_SKILLS_TABLE).get_item(Key={"skillId": skill_id}).get("Item")
    except Exception:
        return False
    if not item or item.get("deleted"):
        return False
    return item.get("workspace_id") == ws


@tool
def list_skill_files(skill_id: str) -> str:
    """List the files that belong to a skill.

    Use this as step 1 when the edit-assistant asks you to optimize,
    review, or rewrite a skill — you'll typically read SKILL.md and a
    handful of index/script files, not the full tree.

    Args:
        skill_id: The skill ID (matches the id field from list_skills).

    Returns:
        JSON ``{"skill_id": ..., "files": [{"path": "...", "size": N}]}``
        on success, ``{"error": ...}`` otherwise.
    """
    deny = require_role(ROLE_VIEWER)
    if deny:
        return json.dumps({"error": "forbidden"})
    if not _skill_in_workspace(skill_id):
        return json.dumps({"error": f"Skill {skill_id} not found in this workspace."})

    s3 = boto3.client("s3", region_name=REGION)
    prefix = f"skills/{skill_id}/"
    out: list[dict[str, Any]] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                rel = obj["Key"][len(prefix) :]
                if not rel:
                    continue
                out.append({"path": rel, "size": obj.get("Size", 0)})
    except Exception as e:
        return json.dumps({"error": f"S3 list failed: {e}"})

    out.sort(key=lambda f: f["path"])
    return json.dumps({"skill_id": skill_id, "files": out}, indent=2, ensure_ascii=False)


@tool
def read_skill_file(skill_id: str, path: str) -> str:
    """Read a single skill file as text.

    Pair with list_skill_files: list first so you know what exists, then
    read only the files that matter for the user's request. Reading a
    dozen small files is fine; don't read every script in a 785-file skill.

    Large files are truncated at 200KB — if truncated, the returned JSON
    includes ``truncated: true`` and the original byte size.

    Args:
        skill_id: The skill ID.
        path: The relative path inside the skill (e.g. "SKILL.md",
            "scripts/render.py"). Must NOT include leading slashes or
            ``..`` segments.

    Returns:
        JSON ``{"skill_id": ..., "path": ..., "content": "...",
        "truncated": bool, "size": N}`` on success, ``{"error": ...}``
        otherwise.
    """
    deny = require_role(ROLE_VIEWER)
    if deny:
        return json.dumps({"error": "forbidden"})
    if not _skill_in_workspace(skill_id):
        return json.dumps({"error": f"Skill {skill_id} not found in this workspace."})

    # Reject traversal — we join with the skill prefix below and an
    # attacker-controlled path like ``../index.json`` would read the
    # workspace-global index, leaking cross-skill metadata.
    if not path or path.startswith("/") or ".." in path.split("/"):
        return json.dumps({"error": f"Invalid path: {path!r}"})

    key = f"skills/{skill_id}/{path}"
    s3 = boto3.client("s3", region_name=REGION)
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=key)
    except Exception:
        return json.dumps({"error": f"File not found: {path}"})

    size = head.get("ContentLength", 0)
    truncated = size > _MAX_FILE_BYTES
    try:
        kwargs: dict[str, Any] = {"Bucket": S3_BUCKET, "Key": key}
        if truncated:
            kwargs["Range"] = f"bytes=0-{_MAX_FILE_BYTES - 1}"
        body = s3.get_object(**kwargs)["Body"].read()
    except Exception as e:
        return json.dumps({"error": f"S3 get failed: {e}"})

    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        return json.dumps(
            {
                "error": f"File is not UTF-8 text ({size} bytes binary): {path}",
            }
        )

    return json.dumps(
        {
            "skill_id": skill_id,
            "path": path,
            "content": content,
            "truncated": truncated,
            "size": size,
        },
        indent=2,
        ensure_ascii=False,
    )
