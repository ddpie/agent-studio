"""write_skill_file / delete_skill_file — mutate files inside an existing skill.

Counterpart to read_skill_file / list_skill_files on the write side. Without
these, the edit-assistant can only change ``SKILL.md`` (via ``update_skill``,
which only rewrites frontmatter + body) — any script-type skill whose bug
lives in ``script.py`` or bundled assets has no editing path, and the
assistant ends up re-creating the whole skill or looping around read calls.

Mirrors ``lambda/crud/skills.py::put_skill_file`` / ``delete_skill_file`` so
the UI and Meta-Agent write the same bytes to the same S3 key (``skills/
{id}/{path}``). Both require ROLE_EDITOR and verify the skill belongs to
the caller's workspace via the same _skill_in_workspace check used by the
read path.
"""

import json
import re
from datetime import datetime, timezone

import boto3
from config import REGION, S3_BUCKET
from strands import tool

from tools._scope import ROLE_EDITOR, current_workspace, require_role

_SKILLS_TABLE = "agent-studio-skills"
# Cap write size to match the DDB item budget + avoid absorbing a user's
# accidentally-pasted 50MB file. Script skills we've seen in practice top
# out around 20KB; 1MB leaves huge headroom without risking runaway costs.
_MAX_WRITE_BYTES = 1_000_000


_SAFE_PATH_RE = re.compile(r"^[^\x00-\x1f\x7f\\]+$")


def _validate_path(path: str) -> str | None:
    """Mirrors lambda/shared/validators.py::validate_path.

    Allows Unicode filenames, blocks control chars / backslashes /
    ``..`` segments / ``//`` sequences. Returns ``None`` on success or
    the error message on failure.
    """
    if not path:
        return "path is required"
    if not _SAFE_PATH_RE.match(path):
        return "Invalid path (control chars or backslashes)"
    if "//" in path or path.startswith("/"):
        return "Invalid path (absolute or double-slash)"
    if ".." in path.split("/"):
        return "Invalid path (traversal)"
    if len(path) > 512:
        return "Path too long (max 512)"
    return None


def _skill_in_workspace(skill_id: str) -> tuple[dict | None, str | None]:
    """Load the skill record and verify workspace ownership.

    Returns ``(item, None)`` on success or ``(None, error)``. The error
    message deliberately doesn't distinguish "doesn't exist" from "other
    workspace" — same enumeration-avoidance pattern used by
    ensure_agent_in_workspace.
    """
    if not skill_id:
        return None, "skill_id is required"
    ws = current_workspace()
    if not ws:
        return None, "No workspace context — refusing to scope this operation."
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        item = ddb.Table(_SKILLS_TABLE).get_item(Key={"skillId": skill_id}).get("Item")
    except Exception as e:
        return None, f"Skill lookup failed: {e}"
    if not item or item.get("deleted") or item.get("workspace_id") != ws:
        return None, f"Skill {skill_id} not found in this workspace."
    return item, None


def _content_type_for(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    if path.endswith(".py"):
        return "text/x-python"
    if path.endswith(".json"):
        return "application/json"
    if path.endswith((".sh", ".bash")):
        return "text/x-shellscript"
    return "text/plain"


def _touch_updated_at(skill_id: str, ws_id: str) -> None:
    """Bump the skill's ``updated_at`` in DDB so the UI's "last modified"
    reflects the write. Best-effort — a DDB failure here shouldn't roll
    back the S3 write the caller just did; they'd have to redo work.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        boto3.resource("dynamodb", region_name=REGION).Table(_SKILLS_TABLE).update_item(
            Key={"skillId": skill_id},
            UpdateExpression="SET updated_at = :now",
            ExpressionAttributeValues={":now": now, ":ws": ws_id},
            ConditionExpression="attribute_exists(skillId) AND workspace_id = :ws",
        )
    except Exception:
        pass


@tool
def write_skill_file(skill_id: str, path: str, content: str) -> str:
    """Overwrite a file inside a skill's S3 tree.

    Use this to fix bugs in a skill's ``script.py``, add a new supporting
    file (``README.md``, ``prompts/system.md``, ``scripts/helpers.py``,
    etc.), or update a bundled asset. **Do NOT** use ``update_skill`` for
    anything other than SKILL.md — that tool only rewrites the
    frontmatter + body of SKILL.md and will leave every other file
    untouched, including script.py.

    Writing to ``SKILL.md`` is allowed but rare — prefer ``update_skill``
    for that one file so the DDB ``name`` / ``description`` columns stay
    in sync automatically.

    Args:
        skill_id: The target skill ID. Must belong to the caller's workspace.
        path: File path inside the skill, relative to ``skills/{id}/``.
            Examples: ``script.py``, ``scripts/render.py``,
            ``assets/template.txt``. Must NOT start with ``/`` or contain
            ``..`` segments. Max 512 chars.
        content: The full new file contents as UTF-8 text. Overwrites the
            file completely; there is no partial / diff mode — send the
            whole file every time. Max 1,000,000 bytes.

    Returns:
        JSON ``{"skill_id": ..., "path": ..., "bytes": N, "updated_at":
        "<ISO>", "status": "written"}`` on success, ``{"error": ...}``
        on failure (workspace mismatch, invalid path, oversize).
    """
    deny = require_role(ROLE_EDITOR)
    if deny:
        return json.dumps(deny)

    item, err = _skill_in_workspace(skill_id)
    if err:
        return json.dumps({"error": err})
    ws_id = item.get("workspace_id", "")

    path_err = _validate_path(path)
    if path_err:
        return json.dumps({"error": path_err})

    if not isinstance(content, str):
        return json.dumps({"error": "content must be a string"})
    body = content.encode("utf-8")
    if len(body) > _MAX_WRITE_BYTES:
        return json.dumps({
            "error": f"content exceeds {_MAX_WRITE_BYTES:,} bytes ({len(body):,} given)",
        })

    key = f"skills/{skill_id}/{path}"
    try:
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=body,
            ContentType=_content_type_for(path),
        )
    except Exception as e:
        return json.dumps({"error": f"S3 write failed: {e}"})

    _touch_updated_at(skill_id, ws_id)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return json.dumps({
        "skill_id": skill_id,
        "path": path,
        "bytes": len(body),
        "updated_at": now,
        "status": "written",
    })


@tool
def delete_skill_file(skill_id: str, path: str) -> str:
    """Delete a single file from a skill's S3 tree.

    Use this to prune obsolete scripts / assets without touching the rest
    of the skill. ``SKILL.md`` cannot be deleted here — a skill must
    always have one. To remove the whole skill use ``delete_skill``.

    Args:
        skill_id: The target skill ID. Must belong to the caller's workspace.
        path: Path inside the skill, relative to ``skills/{id}/``. Same
            validation rules as write_skill_file.

    Returns:
        JSON ``{"skill_id": ..., "path": ..., "status": "deleted"}`` on
        success, ``{"error": ...}`` otherwise.
    """
    deny = require_role(ROLE_EDITOR)
    if deny:
        return json.dumps(deny)

    item, err = _skill_in_workspace(skill_id)
    if err:
        return json.dumps({"error": err})
    ws_id = item.get("workspace_id", "")

    path_err = _validate_path(path)
    if path_err:
        return json.dumps({"error": path_err})
    if path == "SKILL.md":
        return json.dumps({"error": "Cannot delete SKILL.md — use delete_skill for the whole skill"})

    key = f"skills/{skill_id}/{path}"
    try:
        boto3.client("s3", region_name=REGION).delete_object(Bucket=S3_BUCKET, Key=key)
    except Exception as e:
        return json.dumps({"error": f"S3 delete failed: {e}"})

    _touch_updated_at(skill_id, ws_id)
    return json.dumps({
        "skill_id": skill_id,
        "path": path,
        "status": "deleted",
    })
