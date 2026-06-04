"""list_skills — List skills in the caller's workspace, paginated + filterable.

A single workspace can accumulate hundreds of skills (the reference tenant
has 253), and the unfiltered list weighed ~33 KB. When the Meta-Agent calls
this alongside other large tool results in the same turn, the combined
payload has stalled the upstream LLM stream. This tool therefore:

* Sorts items by ``name`` in-process for stable pagination (DDB's
  workspace-index GSI has no sort key, so server-side ordering is not
  guaranteed across pages).
* Accepts ``name_pattern`` (case-insensitive substring) to narrow results.
* Accepts ``limit`` (default 50, clamped 1-200) and ``offset`` (>= 0) for
  paging through large workspaces.
* Returns a dict envelope ``{items, total, filtered, returned, hint}``
  instead of the old bare array — breaking on purpose, since the envelope
  is the only way to carry pagination state back to the caller. All
  in-repo callers are the Meta-Agent itself; the prompt is updated in the
  same commit so the model knows the new shape.

The hint is emitted only when the returned slice is incomplete so the
model doesn't dismiss a clean result as partial.
"""

import json

import boto3
from boto3.dynamodb.conditions import Key
from config import REGION
from strands import tool

from tools._scope import ROLE_VIEWER, current_workspace, require_role

_SKILLS_TABLE = "agent-studio-skills"

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@tool
def list_skills(name_pattern: str = "", limit: int = _DEFAULT_LIMIT, offset: int = 0) -> str:
    """List skills visible to the caller in their current workspace.

    Queries the skills DDB table's workspace-index GSI and returns a
    paginated dict envelope sorted by skill name.

    Args:
        name_pattern: Case-insensitive substring filter on ``name``.
            Empty string means no filter. Default "".
        limit: Maximum items to return. Clamped to [1, 200]. Default 50.
        offset: Number of items to skip after filtering and sorting.
            Must be >= 0. Default 0.

    Returns:
        JSON object ``{items: [...], total: N, filtered: M, returned: K,
        hint?: str}`` where ``items[i] = {id, name, description}``.
        ``total`` is the workspace-wide skill count; ``filtered`` is the
        count after applying ``name_pattern``; ``returned`` is the size
        of the current page. ``hint`` is present only when more items
        exist beyond the current slice.
    """
    deny = require_role(ROLE_VIEWER)
    if deny:
        return json.dumps({"items": [], "total": 0, "filtered": 0, "returned": 0})

    # Clamp inputs so bad values (negative limit, huge offset sent as
    # string "9999") can't spike memory or exceed DDB page budgets.
    try:
        limit_n = max(1, min(int(limit), _MAX_LIMIT))
    except (TypeError, ValueError):
        limit_n = _DEFAULT_LIMIT
    try:
        offset_n = max(0, int(offset))
    except (TypeError, ValueError):
        offset_n = 0
    pattern = (name_pattern or "").strip().lower()

    ws = current_workspace()
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_SKILLS_TABLE)

    all_items: list[dict] = []
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
            for item in resp.get("Items", []):
                if item.get("deleted"):
                    continue
                all_items.append({
                    "id": item.get("skillId", ""),
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                })
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
    except Exception as e:
        return json.dumps({"error": str(e)})

    total = len(all_items)

    if pattern:
        filtered = [s for s in all_items if pattern in s["name"].lower()]
    else:
        filtered = all_items
    filtered_count = len(filtered)

    # Stable sort by name so offset-based paging is deterministic across
    # calls — DDB GSI scan order without a sort key is not.
    filtered.sort(key=lambda s: s["name"].lower())

    page = filtered[offset_n : offset_n + limit_n]
    returned = len(page)

    envelope: dict = {
        "items": page,
        "total": total,
        "filtered": filtered_count,
        "returned": returned,
    }

    remaining = filtered_count - (offset_n + returned)
    if remaining > 0:
        next_offset = offset_n + returned
        if pattern:
            envelope["hint"] = (
                f"Showing {returned} of {filtered_count} matches (total {total} skills). "
                f"Pass offset={next_offset} for the next page."
            )
        else:
            envelope["hint"] = (
                f"Showing {returned} of {total} skills. "
                f"Pass offset={next_offset} for the next page, or name_pattern to filter."
            )
    elif offset_n > 0 and returned == 0 and filtered_count > 0:
        # offset past the end — surface it so the model can back off
        envelope["hint"] = (
            f"offset={offset_n} is beyond the last item (filtered={filtered_count}). "
            "Pass a smaller offset."
        )

    return json.dumps(envelope, indent=2, ensure_ascii=False)
