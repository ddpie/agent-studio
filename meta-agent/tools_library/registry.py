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
