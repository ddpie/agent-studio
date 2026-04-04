/**
 * Tool Library Store — manages tool templates (DynamoDB-backed).
 * Tools are either builtin (from tools_library) or user-created.
 */
import { create } from "zustand";
import { scanAllTools, putToolItem, deleteToolItem, type ToolTemplate } from "../lib/tool-storage";
import { invalidateToolCatalogCache, writeToolCatalog, type ToolCatalog } from "../lib/s3-utils";

export type { ToolTemplate } from "../lib/tool-storage";

interface ToolLibraryState {
  tools: ToolTemplate[];
  loading: boolean;
  saving: boolean;
  error: string | null;

  fetchTools: () => Promise<void>;
  saveTool: (tool: ToolTemplate) => Promise<void>;
  deleteTool: (id: string) => Promise<void>;
  clearError: () => void;
}

/** Build S3 catalog JSON from tool list */
function buildCatalog(tools: ToolTemplate[]): ToolCatalog {
  const catalog: ToolCatalog = {};
  for (const t of tools) {
    catalog[t.id] = {
      id: t.id,
      name: t.name,
      description: t.description,
      category: t.category,
      code: t.code,
      builtin: t.builtin,
      owner: t.owner,
      visibility: t.visibility,
    };
  }
  return catalog;
}

export const useToolLibraryStore = create<ToolLibraryState>((set) => ({
  tools: [],
  loading: false,
  saving: false,
  error: null,

  fetchTools: async () => {
    set({ loading: true, error: null });
    try {
      const tools = await scanAllTools();
      tools.sort((a, b) => {
        // Builtin first, then by name
        if (a.builtin !== b.builtin) return a.builtin ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      set({ tools, loading: false });
    } catch (err) {
      console.error("Failed to fetch tools:", err);
      set({ tools: [], loading: false, error: err instanceof Error ? err.message : "Failed to load tools" });
    }
  },

  saveTool: async (tool) => {
    set({ saving: true, error: null });
    try {
      await putToolItem(tool);
      // Refresh list from DDB
      const tools = await scanAllTools();
      tools.sort((a, b) => {
        if (a.builtin !== b.builtin) return a.builtin ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      set({ tools, saving: false });
      // Sync S3 catalog (non-blocking)
      invalidateToolCatalogCache();
      writeToolCatalog(buildCatalog(tools)).catch((e) =>
        console.warn("Failed to sync S3 catalog:", e)
      );
    } catch (err) {
      console.error("Failed to save tool:", err);
      set({ saving: false, error: err instanceof Error ? err.message : "Failed to save tool" });
      throw err;
    }
  },

  deleteTool: async (id) => {
    set({ saving: true, error: null });
    try {
      await deleteToolItem(id);
      const tools = await scanAllTools();
      tools.sort((a, b) => {
        if (a.builtin !== b.builtin) return a.builtin ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      set({ tools, saving: false });
      invalidateToolCatalogCache();
      writeToolCatalog(buildCatalog(tools)).catch((e) =>
        console.warn("Failed to sync S3 catalog:", e)
      );
    } catch (err) {
      console.error("Failed to delete tool:", err);
      set({ saving: false, error: err instanceof Error ? err.message : "Failed to delete tool" });
      throw err;
    }
  },

  clearError: () => set({ error: null }),
}));
