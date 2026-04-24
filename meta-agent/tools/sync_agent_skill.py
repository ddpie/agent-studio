"""sync_agent_skill — Refresh an agent's private skill copy from the library.

When a user attaches a library skill to an agent, the CRUD layer copies the
skill files into ``agents/{agent_id}/skills/{local_skill_id}/`` so the agent
owns an immutable snapshot. Later edits to the library skill do NOT propagate
to attached agents. This tool re-copies the library version on top of the
agent's private copy and triggers a redeploy so the baked-in ``## Skill:``
section in ``prompt.txt`` picks up the new SKILL.md.

Rebind semantics: callers usually identify the skill by ``name`` (matching
``ppt-generator`` in DataAnalyst's metadata to ``ppt-generator`` in the
current library). The attached copy's ``sourceSkillId`` may point to a
library skill that was since deleted — in that case we rebind to the new
library id under the same name. Pass ``new_source_skill_id`` to force a
specific rebind target when multiple library skills share a name.
"""

import hashlib
import json
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ensure_agent_in_workspace, current_workspace, ROLE_EDITOR
from tools.update_agent import update_agent as _update_agent


_SKILLS_TABLE = "agent-studio-skills"


def _compute_content_hash(files: dict[str, str]) -> str:
    """SHA-256 over ``path:content`` lines sorted by path, first 8 hex chars.

    Matches ``computeSkillHash`` in frontend/src/lib/agent-skill-storage.ts so
    agent metadata written from either side stays consistent.
    """
    combined = "\n".join(f"{p}:{files[p]}" for p in sorted(files))
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    return digest[:8]


def _read_library_skill_files(s3, skill_id: str) -> dict[str, str]:
    """Read every file under ``skills/{skill_id}/`` as UTF-8 text.

    Binary files (icons, fonts) would fail .decode — we skip them and note
    that in the returned sentinel so callers know the hash is over text only.
    The hash only needs to detect *text* drift; binaries round-trip byte-for-
    byte through copy_object regardless.
    """
    prefix = f"skills/{skill_id}/"
    out: dict[str, str] = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            rel = key[len(prefix):]
            if not rel:
                continue
            body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()
            try:
                out[rel] = body.decode("utf-8")
            except UnicodeDecodeError:
                # Binary asset — keep a stable placeholder so the hash is
                # deterministic without reading the bytes into memory twice.
                out[rel] = f"__binary__:{len(body)}"
    return out


def _resolve_library_skill(name: str, explicit_id: str) -> tuple[dict | None, str | None]:
    """Find the current library skill to sync from.

    If ``explicit_id`` is given, trust it (but still verify workspace scope).
    Otherwise query the skills DDB by name within the caller's workspace.
    """
    ws = current_workspace()
    if not ws:
        return None, "No workspace scope — refusing to resolve library skill."

    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_SKILLS_TABLE)

    if explicit_id:
        try:
            item = table.get_item(Key={"skillId": explicit_id}).get("Item")
        except Exception as e:
            return None, f"Skill lookup failed: {e}"
        if not item or item.get("deleted"):
            return None, f"Library skill {explicit_id} not found."
        if item.get("workspace_id") != ws:
            return None, f"Library skill {explicit_id} is in a different workspace."
        return item, None

    # Query by workspace, filter by name. Can't use a secondary index here
    # because there isn't a workspace+name GSI — scan-within-query is fine
    # since library skills per workspace are O(tens), not thousands.
    matches: list[dict] = []
    last_key = None
    try:
        while True:
            kwargs = {
                "IndexName": "workspace-index",
                "KeyConditionExpression": Key("workspace_id").eq(ws),
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            resp = table.query(**kwargs)
            for it in resp.get("Items", []):
                if it.get("deleted"):
                    continue
                if it.get("name") == name:
                    matches.append(it)
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
    except Exception as e:
        return None, f"Skill query failed: {e}"

    if not matches:
        return None, f"No library skill named '{name}' in this workspace."
    if len(matches) > 1:
        ids = [m.get("skillId", "") for m in matches]
        return None, (
            f"Multiple library skills named '{name}': {ids}. "
            "Pass new_source_skill_id to disambiguate."
        )
    return matches[0], None


def _delete_prefix(s3, prefix: str) -> int:
    """Delete every object under ``prefix``. Returns count deleted."""
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[dict] = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append({"Key": obj["Key"]})
    deleted = 0
    # delete_objects caps at 1000 keys per call.
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        if not batch:
            continue
        s3.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": batch})
        deleted += len(batch)
    return deleted


def _copy_prefix(s3, src_prefix: str, dst_prefix: str) -> int:
    """Server-side copy every object under ``src_prefix`` to ``dst_prefix``."""
    paginator = s3.get_paginator("list_objects_v2")
    copied = 0
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=src_prefix):
        for obj in page.get("Contents", []) or []:
            src_key = obj["Key"]
            rel = src_key[len(src_prefix):]
            if not rel:
                continue
            s3.copy_object(
                Bucket=S3_BUCKET,
                CopySource={"Bucket": S3_BUCKET, "Key": src_key},
                Key=f"{dst_prefix}{rel}",
            )
            copied += 1
    return copied


@tool
def sync_agent_skill(
    agent_id: str,
    skill_name: str,
    new_source_skill_id: str = "",
    redeploy: bool = True,
) -> str:
    """Re-sync an agent's attached skill from the library's current version.

    Use when the user says things like "把 X agent 里的 Y skill 更新到最新"
    or "the ppt-generator attached to DataAnalyst is stale — sync it". The
    tool:

      1. Looks up the agent's metadata and finds the attached skill matching
         ``skill_name`` (case-sensitive).
      2. Resolves the library version to sync from — by ``skill_name`` by
         default, or by ``new_source_skill_id`` if provided (use the explicit
         form when the original library skill was deleted and you want to
         rebind to a replacement).
      3. Overwrites the agent's private skill copy at
         ``agents/{agent_id}/skills/{local_skill_id}/`` with the library
         version — old files that no longer exist in the library are deleted.
      4. Updates ``metadata.json`` so ``sourceSkillId`` / ``sourceContentHash``
         / ``contentHash`` / ``files`` reflect the new copy.
      5. Triggers a redeploy (``redeploy=True`` by default) so the baked-in
         SKILL.md prompt section refreshes. Pass ``redeploy=False`` when you
         only need scripts refreshed (agents read ``scripts/*`` from S3 at
         runtime, so a redeploy isn't strictly required — but prompt drift
         will remain until the next deploy).

    Args:
        agent_id: Target agent runtime ID (e.g. "DataAnalyst-bCBR743Mvj").
        skill_name: Name of the skill as attached to the agent (matches the
            ``name`` field in the agent's skills manifest).
        new_source_skill_id: Optional explicit library skill ID to sync from.
            Use when the attached skill's original source was deleted or when
            multiple library skills share a name.
        redeploy: Trigger ``update_agent`` after the sync so the prompt
            section refreshes. Default True. Set False for script-only
            updates where immediate availability matters more than prompt
            accuracy.

    Returns:
        JSON with sync status, old/new contentHash, file delta counts, and
        the update_agent result when redeploy=True.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    s3 = boto3.client("s3", region_name=REGION)

    # 1. Load agent metadata
    try:
        md_obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        metadata = json.loads(md_obj["Body"].read().decode("utf-8"))
    except Exception as e:
        return json.dumps({"error": f"metadata.json not found for {agent_id}: {e}"})

    skills_list = metadata.get("skills", []) or []
    target_idx = next(
        (i for i, s in enumerate(skills_list) if s.get("name") == skill_name),
        -1,
    )
    if target_idx < 0:
        attached = [s.get("name", "") for s in skills_list]
        return json.dumps({
            "error": f"Agent {agent_id} has no attached skill named '{skill_name}'.",
            "attached_skills": attached,
        })
    attached_skill = skills_list[target_idx]
    local_skill_id = attached_skill.get("id", "")
    if not local_skill_id:
        return json.dumps({
            "error": f"Attached skill '{skill_name}' has no local id — metadata corrupted.",
        })
    old_source_id = attached_skill.get("sourceSkillId", "")
    old_content_hash = attached_skill.get("contentHash", "")

    # 2. Resolve library source
    library_item, resolve_err = _resolve_library_skill(skill_name, new_source_skill_id)
    if resolve_err:
        return json.dumps({"error": resolve_err})
    library_skill_id = library_item["skillId"]
    library_desc = library_item.get("description", attached_skill.get("description", ""))

    # 3. Read library files to compute the post-sync hash and to verify the
    # library actually has content. If the library skill's prefix is empty
    # we refuse the sync — overwriting an agent's working skill with empty
    # on an accidental library prune would be destructive.
    library_files = _read_library_skill_files(s3, library_skill_id)
    if not library_files:
        return json.dumps({
            "error": f"Library skill {library_skill_id} has no files under "
                     f"skills/{library_skill_id}/ — refusing to sync.",
        })
    new_content_hash = _compute_content_hash(library_files)

    if new_content_hash == old_content_hash and old_source_id == library_skill_id:
        return json.dumps({
            "agent_id": agent_id,
            "skill_name": skill_name,
            "status": "noop",
            "message": "Agent copy already matches library version — no sync needed.",
            "contentHash": old_content_hash,
        })

    # 4. Overwrite the agent's private copy: wipe then copy. Doing delete
    # before copy avoids leaving behind files that the library removed
    # (e.g. ``data-analysis-guide`` was renamed — stale file would linger).
    dst_prefix = f"agents/{agent_id}/skills/{local_skill_id}/"
    src_prefix = f"skills/{library_skill_id}/"

    try:
        deleted = _delete_prefix(s3, dst_prefix)
    except Exception as e:
        return json.dumps({"error": f"Failed to clear old skill copy: {e}"})

    try:
        copied = _copy_prefix(s3, src_prefix, dst_prefix)
    except Exception as e:
        # Partial copy leaves the agent in a broken state. Surface clearly.
        return json.dumps({
            "error": f"Copy failed after deleting old copy: {e}. "
                     f"Agent skill directory is now empty — retry the sync.",
        })

    # 5. Update the agent's skills manifest in-place. We keep local_skill_id
    # stable so any cached references (e.g. log correlations) still resolve.
    new_files = sorted(library_files.keys())
    skills_list[target_idx] = {
        "id": local_skill_id,
        "name": skill_name,
        "description": library_desc,
        "sourceSkillId": library_skill_id,
        "sourceContentHash": new_content_hash,
        "contentHash": new_content_hash,
        "files": new_files,
    }
    metadata["skills"] = skills_list
    deployed = metadata.get("deployedSkillHashes", {}) or {}
    # deployedSkillHashes is keyed by local id; keep that convention.
    deployed[local_skill_id] = new_content_hash
    metadata["deployedSkillHashes"] = deployed
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    result: dict = {
        "agent_id": agent_id,
        "skill_name": skill_name,
        "local_skill_id": local_skill_id,
        "old_source_skill_id": old_source_id,
        "new_source_skill_id": library_skill_id,
        "old_content_hash": old_content_hash,
        "new_content_hash": new_content_hash,
        "files_deleted": deleted,
        "files_copied": copied,
        "status": "synced",
    }

    # 6. Optional redeploy to refresh the baked-in prompt section.
    if redeploy:
        try:
            redeploy_raw = _update_agent(
                agent_id=agent_id,
                agent_name=metadata.get("name", ""),
            )
            try:
                result["redeploy"] = json.loads(redeploy_raw)
            except Exception:
                result["redeploy"] = {"raw": redeploy_raw}
        except Exception as e:
            result["redeploy"] = {"error": f"update_agent failed: {e}"}
    else:
        result["redeploy"] = {
            "skipped": True,
            "note": "scripts/ will be picked up on next invocation; "
                    "prompt's ## Skill: section is still stale until next deploy.",
        }

    return json.dumps(result, indent=2, ensure_ascii=False)
