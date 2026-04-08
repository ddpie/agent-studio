/**
 * Tool Library operations — CRUD for tool templates via Lambda API.
 */
import {
  fetchTools,
  invalidateToolsCache,
  createOrUpdateTool,
  deleteToolApi,
  restoreToolApi,
  type ToolItem,
} from "./api-client";

export interface ToolTemplate {
  id: string;        // = @tool function name (PK)
  name: string;
  description: string;
  category: string;
  code: string;
  builtin: boolean;
  owner: string;
  visibility: string;
  created_at: string;
  updated_at: string;
  deleted?: boolean;
  deleted_at?: string;
}

/** Map Lambda ToolItem to frontend ToolTemplate */
function toTemplate(item: ToolItem): ToolTemplate {
  return {
    id: item.toolId,
    name: item.name || "",
    description: item.description || "",
    category: item.category || "custom",
    code: item.code || "",
    builtin: item.builtin || false,
    owner: item.owner || "",
    visibility: item.visibility || "shared",
    created_at: item.created_at || "",
    updated_at: item.updated_at || "",
    deleted: item.deleted || false,
    deleted_at: item.deleted_at || "",
  };
}

/** Fetch all tools from Lambda API */
export async function scanAllTools(): Promise<ToolTemplate[]> {
  try {
    invalidateToolsCache();
    const resp = await fetchTools(undefined, 200);
    return resp.items.map(toTemplate);
  } catch (err) {
    console.error("scanAllTools failed:", err);
    return [];
  }
}

/** Save (create or update) a tool template */
export async function putToolItem(tool: ToolTemplate): Promise<void> {
  try {
    const data: Record<string, any> = {
      name: tool.name,
      description: tool.description,
      category: tool.category || "custom",
      code: tool.code,
    };
    if (tool.id) {
      data.toolId = tool.id;
      data.expected_updated_at = tool.updated_at;
    }
    await createOrUpdateTool(data);
  } catch (err) {
    console.error("putToolItem failed:", err);
    throw err;
  }
}

/** Soft-delete a tool */
export async function softDeleteToolItem(toolId: string): Promise<void> {
  try {
    const ok = await deleteToolApi(toolId);
    if (!ok) throw new Error("Failed to delete tool");
  } catch (err) {
    console.error("softDeleteToolItem failed:", err);
    throw err;
  }
}

/** Hard-delete a tool (same as soft-delete via API — backend only does soft-delete) */
export async function deleteToolItem(toolId: string): Promise<void> {
  try {
    const ok = await deleteToolApi(toolId);
    if (!ok) throw new Error("Failed to delete tool");
  } catch (err) {
    console.error("deleteToolItem failed:", err);
    throw err;
  }
}

/** Restore a soft-deleted tool */
export async function restoreToolItem(toolId: string): Promise<void> {
  try {
    const ok = await restoreToolApi(toolId);
    if (!ok) throw new Error("Failed to restore tool");
  } catch (err) {
    console.error("restoreToolItem failed:", err);
    throw err;
  }
}
