"""Tool library registry — list all available pre-built tools."""

import json
from strands import tool

from tools_library import web_search, fetch_webpage, s3_read, sql_readonly, translate, chart_generator

_ALL_TOOLS = [
    web_search,
    fetch_webpage,
    s3_read,
    sql_readonly,
    translate,
    chart_generator,
]


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
    """Get the Python code for a tool by its function name (e.g., 's3_read')."""
    for mod in _ALL_TOOLS:
        if func_name in [n.strip() for n in mod.TOOL_NAMES.split(",")]:
            return mod.TOOL_CODE
    return None


def get_tool_code(tool_id: str) -> str | None:
    """Get the Python code for a tool by its ID."""
    for mod in _ALL_TOOLS:
        if mod.TOOL_META["id"] == tool_id:
            return mod.TOOL_CODE
    return None


def get_tool_names(tool_id: str) -> str | None:
    """Get the tool function names for a tool by its ID."""
    for mod in _ALL_TOOLS:
        if mod.TOOL_META["id"] == tool_id:
            return mod.TOOL_NAMES
    return None


def assemble_tools(tool_ids: list[str]) -> tuple[str, str]:
    """Assemble tool code and names from a list of tool IDs.

    Returns:
        (tool_definitions, tool_names) — ready to pass to create_agent/update_agent.
    """
    codes = []
    names = []
    for tid in tool_ids:
        for mod in _ALL_TOOLS:
            if mod.TOOL_META["id"] == tid:
                codes.append(mod.TOOL_CODE.strip())
                names.append(mod.TOOL_NAMES)
                break
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
            }
    return catalog


def upload_tool_catalog():
    """Generate and upload tool-catalog.json to S3."""
    import boto3
    from config import REGION, S3_BUCKET

    catalog = build_tool_catalog()
    s3 = boto3.client("s3", region_name=REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key="agents/base/tool-catalog.json",
        Body=json.dumps(catalog, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return len(catalog)
