"""Tool library registry — list all available pre-built tools."""

import json
from strands import tool

from tools_library import web_search, fetch_webpage, s3_read, sql_readonly, translate, chart_generator, agent_caller

_ALL_TOOLS = [
    web_search,
    fetch_webpage,
    s3_read,
    sql_readonly,
    translate,
    chart_generator,
    agent_caller,
]

# Pre-built indexes for O(1) lookup instead of linear scan
_FUNC_NAME_INDEX: dict[str, str] = {}  # func_name -> TOOL_CODE
_TOOL_ID_INDEX: dict[str, object] = {}  # tool_id -> module
for _mod in _ALL_TOOLS:
    _TOOL_ID_INDEX[_mod.TOOL_META["id"]] = _mod
    for _fname in _mod.TOOL_NAMES.split(","):
        _fname = _fname.strip()
        if _fname:
            _FUNC_NAME_INDEX[_fname] = _mod.TOOL_CODE

_ddb_client = None

def _get_ddb_client():
    """Lazy-init and cache DynamoDB client."""
    global _ddb_client
    if _ddb_client is None:
        import boto3
        from config import REGION
        _ddb_client = boto3.client("dynamodb", region_name=REGION)
    return _ddb_client


def _get_tools_table():
    from config import TOOLS_TABLE
    return TOOLS_TABLE


@tool
def list_tool_library() -> str:
    """List all pre-built tools available in the tool library.

    These tools can be added to agents during creation or update.
    Returns a JSON array of available tools with id, name, description, and category.
    """
    catalog = []
    for mod in _ALL_TOOLS:
        meta = mod.TOOL_META
        catalog.append({
            "id": meta["id"],
            "name": meta["name"],
            "description": meta["description"],
            "category": meta["category"],
            "tool_names": mod.TOOL_NAMES,
        })
    return json.dumps(catalog, indent=2, ensure_ascii=False)


@tool
def get_tool_library_code(tool_ids: str) -> str:
    """Get the Python source code for one or more built-in tools.

    Use this to include built-in tool code in tool_definitions when creating or updating agents.
    The returned code contains @tool decorated functions ready to be used.

    Args:
        tool_ids: Comma-separated tool IDs from list_tool_library (e.g., "s3_read,generate_chart").

    Returns:
        Combined Python code for all requested tools, or error if tool not found.
    """
    ids = [t.strip() for t in tool_ids.split(",") if t.strip()]
    if not ids:
        return json.dumps({"error": "No tool IDs provided. Call list_tool_library first."})

    codes = []
    not_found = []
    for tid in ids:
        code = get_tool_code(tid)
        if code:
            codes.append(code.strip())
        else:
            # Try by function name
            code = get_tool_code_by_func_name(tid)
            if code:
                codes.append(code.strip())
            else:
                not_found.append(tid)

    result = "\n\n".join(codes)
    if not_found:
        result += f"\n\n# WARNING: Tools not found: {', '.join(not_found)}"
    return result


def get_tool_code_by_func_name(func_name: str) -> str | None:
    """Get the Python code for a tool by its function name (e.g., 's3_read').

    Checks in-memory registry first, then falls back to DynamoDB for user-created tools.
    """
    code = _FUNC_NAME_INDEX.get(func_name)
    if code:
        return code
    # Fallback: check DynamoDB for user-created tools
    try:
        ddb = _get_ddb_client()
        resp = ddb.get_item(
            TableName=_get_tools_table(),
            Key={"toolId": {"S": func_name}},
        )
        item = resp.get("Item")
        if item and "code" in item:
            return item["code"]["S"]
    except Exception as e:
        print(f"Warning: DDB fallback failed for tool '{func_name}': {e}")
    return None


def get_tool_code(tool_id: str) -> str | None:
    """Get the Python code for a tool by its ID."""
    mod = _TOOL_ID_INDEX.get(tool_id)
    return mod.TOOL_CODE if mod else None


def get_tool_names(tool_id: str) -> str | None:
    """Get the tool function names for a tool by its ID."""
    mod = _TOOL_ID_INDEX.get(tool_id)
    return mod.TOOL_NAMES if mod else None


def assemble_tools(tool_ids: list[str]) -> tuple[str, str]:
    """Assemble tool code and names from a list of tool IDs.

    Returns:
        (tool_definitions, tool_names) — ready to pass to create_agent/update_agent.
    """
    codes = []
    names = []
    for tid in tool_ids:
        mod = _TOOL_ID_INDEX.get(tid)
        if mod:
            codes.append(mod.TOOL_CODE.strip())
            names.append(mod.TOOL_NAMES)
    return "\n\n".join(codes), ",".join(names)


def build_tool_catalog() -> dict:
    """Build a catalog dict mapping function names to their metadata + code.

    Used to generate base/tool-catalog.json for the frontend.
    """
    catalog = {}
    for mod in _ALL_TOOLS:
        meta = mod.TOOL_META
        for func_name in [n.strip() for n in mod.TOOL_NAMES.split(",") if n.strip()]:
            catalog[func_name] = {
                "id": meta["id"],
                "name": meta["name"],
                "description": meta["description"],
                "category": meta["category"],
                "code": mod.TOOL_CODE.strip(),
                "builtin": True,
            }
    return catalog


def upload_tool_catalog():
    """Sync built-in tools to DynamoDB, then generate and upload tool-catalog.json to S3.

    1. Upsert built-in tools to DDB (only overwrite existing builtin entries, not user tools)
    2. Scan all tools from DDB (builtin + user-created)
    3. Upload combined catalog to S3
    """
    import boto3
    from config import REGION, S3_BUCKET

    ddb = _get_ddb_client()
    table = _get_tools_table()
    now = __import__("datetime").datetime.utcnow().isoformat() + "Z"

    # 1. Seed built-in tools to DDB (only if not already present)
    for mod in _ALL_TOOLS:
        meta = mod.TOOL_META
        for func_name in [n.strip() for n in mod.TOOL_NAMES.split(",") if n.strip()]:
            try:
                ddb.put_item(
                    TableName=table,
                    Item={
                        "toolId": {"S": func_name},
                        "name": {"S": meta["name"]},
                        "description": {"S": meta["description"]},
                        "category": {"S": meta["category"]},
                        "code": {"S": mod.TOOL_CODE.strip()},
                        "builtin": {"BOOL": True},
                        "owner": {"S": "__builtin__"},
                        "visibility": {"S": "shared"},
                        "created_at": {"S": now},
                        "updated_at": {"S": now},
                    },
                    ConditionExpression="attribute_not_exists(toolId)",
                )
            except ddb.exceptions.ConditionalCheckFailedException:
                # User has a tool with the same name — don't overwrite
                pass
            except Exception as e:
                print(f"Warning: Failed to upsert tool '{func_name}' to DDB: {e}")

    # 2. Scan all tools from DDB to build catalog
    catalog = {}
    try:
        paginator = ddb.get_paginator("scan")
        for page in paginator.paginate(TableName=table):
            for item in page.get("Items", []):
                tool_id = item["toolId"]["S"]
                catalog[tool_id] = {
                    "id": tool_id,
                    "name": item.get("name", {}).get("S", tool_id),
                    "description": item.get("description", {}).get("S", ""),
                    "category": item.get("category", {}).get("S", "custom"),
                    "code": item.get("code", {}).get("S", ""),
                    "builtin": item.get("builtin", {}).get("BOOL", False),
                }
    except Exception as e:
        print(f"Warning: DDB scan failed, falling back to in-memory catalog: {e}")
        catalog = build_tool_catalog()

    # 3. Upload to S3
    s3 = boto3.client("s3", region_name=REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key="agents/base/tool-catalog.json",
        Body=json.dumps(catalog, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return len(catalog)
