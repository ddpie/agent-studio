"""attach_agent_skill — Attach a library skill to an agent that doesn't have it.

Complement to sync_agent_skill: sync *refreshes* an already-attached skill
from the library, attach *adds* a library skill the agent has never had.

Why two tools instead of one with a flag: the two intents carry different
failure modes — attach-when-already-attached should route the user to sync
(otherwise they get a duplicate), and sync-when-not-attached should say
"did you mean attach". Keeping them separate lets the Meta-Agent pick the
right one from user phrasing without a brittle else-branch in a single tool.
"""

import json
import uuid
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ensure_agent_in_workspace, ROLE_EDITOR
from tools.sync_agent_skill import (
    _resolve_library_skill,
    _read_library_skill_files,
    _copy_prefix,
    _compute_content_hash,
)
from tools.update_agent import update_agent as _update_agent


@tool
def attach_agent_skill(
    agent_id: str,
    skill_name: str,
    new_source_skill_id: str = "",
    redeploy: bool = True,
) -> str:
    """Attach a library skill to an agent that doesn't already have it.

    Use when the user says "给 X agent 加上 Y skill" / "add the ppt-generator
    skill to DataAnalyst" and ``list_skills`` shows the skill exists in the
    library but the agent doesn't currently have it. This tool:

      1. Refuses if a skill with the same ``name`` is already attached —
         that's a sync operation, not an attach, so the user gets an error
         directing them to ``sync_agent_skill``.
      2. Resolves the library source (by ``skill_name`` unless
         ``new_source_skill_id`` is given).
      3. Copies library files to a fresh
         ``agents/{agent_id}/skills/{new_local_id}/`` prefix (8-char UUID,
         matching the frontend's ``copySkillToAgent`` convention).
      4. Appends a new entry to the agent's skills manifest.
      5. Redeploys (unless ``redeploy=False``) so the baked-in prompt picks
         up the new ``## Skill:`` section. Without redeploy, runtime
         ``load_skill(name)`` still works immediately because the sub-agent
         reads the manifest and scripts from S3 — but the system prompt
         won't mention the new skill until the next deploy, which blunts
         the model's ability to know it's available.

    Args:
        agent_id: Target agent runtime ID.
        skill_name: Skill to attach. Must match the ``name`` of a skill in
            the caller's workspace library (see ``list_skills``).
        new_source_skill_id: Optional explicit library skill ID. Use when
            multiple library skills share a name.
        redeploy: Trigger ``update_agent`` after the attach. Default True.

    Returns:
        JSON with attach status, new local skill id, contentHash, file
        count, and the update_agent result when redeploy=True.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    s3 = boto3.client("s3", region_name=REGION)

    # 1. Load agent metadata and refuse if already attached.
    try:
        md_obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        metadata = json.loads(md_obj["Body"].read().decode("utf-8"))
    except Exception as e:
        return json.dumps({"error": f"metadata.json not found for {agent_id}: {e}"})

    skills_list = metadata.get("skills", []) or []
    if any(s.get("name") == skill_name for s in skills_list):
        return json.dumps({
            "error": (
                f"Agent {agent_id} already has a skill named '{skill_name}'. "
                "Use sync_agent_skill to refresh it from the library, or "
                "rename/remove the existing one first."
            ),
        })

    # 2. Resolve library source.
    library_item, resolve_err = _resolve_library_skill(skill_name, new_source_skill_id)
    if resolve_err:
        return json.dumps({"error": resolve_err})
    library_skill_id = library_item["skillId"]
    library_desc = library_item.get("description", "")

    # 3. Read library files + compute hash.
    library_files = _read_library_skill_files(s3, library_skill_id)
    if not library_files:
        return json.dumps({
            "error": f"Library skill {library_skill_id} has no files — nothing to attach.",
        })
    content_hash = _compute_content_hash(library_files)

    # 4. Allocate a fresh local id and copy. Match the frontend convention
    # (uuid4().slice(0, 8)) so ids from both attach paths look uniform.
    new_local_id = uuid.uuid4().hex[:8]
    dst_prefix = f"agents/{agent_id}/skills/{new_local_id}/"
    src_prefix = f"skills/{library_skill_id}/"
    try:
        copied = _copy_prefix(s3, src_prefix, dst_prefix)
    except Exception as e:
        return json.dumps({"error": f"Copy failed: {e}"})

    # 5. Append to the manifest.
    new_files = sorted(library_files.keys())
    new_entry = {
        "id": new_local_id,
        "name": skill_name,
        "description": library_desc,
        "sourceSkillId": library_skill_id,
        "sourceContentHash": content_hash,
        "contentHash": content_hash,
        "files": new_files,
    }
    skills_list.append(new_entry)
    metadata["skills"] = skills_list
    deployed = metadata.get("deployedSkillHashes", {}) or {}
    deployed[new_local_id] = content_hash
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
        "local_skill_id": new_local_id,
        "source_skill_id": library_skill_id,
        "content_hash": content_hash,
        "files_copied": copied,
        "status": "attached",
    }

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
            "note": "Manifest and files are live; system prompt won't mention "
                    "the new skill until the next deploy.",
        }

    return json.dumps(result, indent=2, ensure_ascii=False)
