/**
 * Tool Library Store — manages tool templates (DynamoDB-backed).
 * Tools are either builtin (from tools_library) or user-created.
 */
import { create } from "zustand";
import { scanAllTools, putToolItem, deleteToolItem, softDeleteToolItem, restoreToolItem, type ToolTemplate } from "../lib/tool-storage";
import { invalidateToolCatalogCache, writeToolCatalog, type ToolCatalog } from "../lib/s3-utils";

export type { ToolTemplate } from "../lib/tool-storage";

function sortTools(tools: ToolTemplate[]): ToolTemplate[] {
  return tools.sort((a, b) => a.name.localeCompare(b.name));
}

interface ToolLibraryState {
  tools: ToolTemplate[];
  trashedTools: ToolTemplate[];
  loading: boolean;
  saving: boolean;
  error: string | null;

  fetchTools: () => Promise<void>;
  saveTool: (tool: ToolTemplate) => Promise<void>;
  deleteTool: (id: string) => Promise<void>;
  softDeleteTool: (id: string) => Promise<void>;
  restoreTool: (id: string) => Promise<void>;
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
  trashedTools: [],
  loading: false,
  saving: false,
  error: null,

  fetchTools: async () => {
    set({ loading: true, error: null });
    try {
      const all = sortTools(await scanAllTools());
      set({
        tools: all.filter(t => !t.deleted),
        trashedTools: all.filter(t => t.deleted),
        loading: false,
      });
    } catch (err) {
      console.error("Failed to fetch tools:", err);
      set({ tools: [], trashedTools: [], loading: false, error: err instanceof Error ? err.message : "Failed to load tools" });
    }
  },

  saveTool: async (tool) => {
    set({ saving: true, error: null });
    try {
      await putToolItem(tool);
      const tools = sortTools(await scanAllTools());
      set({ tools, saving: false });
      // Sync S3 catalog after write succeeds
      writeToolCatalog(buildCatalog(tools))
        .then(() => invalidateToolCatalogCache())
        .catch((e) => console.warn("Failed to sync S3 catalog:", e));
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
      const all = sortTools(await scanAllTools());
      set({ tools: all.filter(t => !t.deleted), trashedTools: all.filter(t => t.deleted), saving: false });
      writeToolCatalog(buildCatalog(all.filter(t => !t.deleted)))
        .then(() => invalidateToolCatalogCache())
        .catch((e) => console.warn("Failed to sync S3 catalog:", e));
    } catch (err) {
      console.error("Failed to delete tool:", err);
      set({ saving: false, error: err instanceof Error ? err.message : "Failed to delete tool" });
      throw err;
    }
  },

  softDeleteTool: async (id) => {
    set({ saving: true, error: null });
    try {
      await softDeleteToolItem(id);
      const all = sortTools(await scanAllTools());
      const active = all.filter(t => !t.deleted);
      set({ tools: active, trashedTools: all.filter(t => t.deleted), saving: false });
      writeToolCatalog(buildCatalog(active))
        .then(() => invalidateToolCatalogCache())
        .catch((e) => console.warn("Failed to sync S3 catalog:", e));
    } catch (err) {
      console.error("Failed to soft-delete tool:", err);
      set({ saving: false, error: err instanceof Error ? err.message : "Failed to delete tool" });
      throw err;
    }
  },

  restoreTool: async (id) => {
    set({ saving: true, error: null });
    try {
      await restoreToolItem(id);
      const all = sortTools(await scanAllTools());
      const active = all.filter(t => !t.deleted);
      set({ tools: active, trashedTools: all.filter(t => t.deleted), saving: false });
      writeToolCatalog(buildCatalog(active))
        .then(() => invalidateToolCatalogCache())
        .catch((e) => console.warn("Failed to sync S3 catalog:", e));
    } catch (err) {
      console.error("Failed to restore tool:", err);
      set({ saving: false, error: err instanceof Error ? err.message : "Failed to restore tool" });
      throw err;
    }
  },

  clearError: () => set({ error: null }),
}));
