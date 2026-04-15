import { create } from "zustand";
import { fetchAgentMetadata, type AgentMetadata, type AgentSkillEntry } from "../lib/agent-metadata";
import { extractToolsFromDeployment } from "../lib/tool-extractor";
import { fetchTools, deleteAgentSkillFiles } from "../lib/api-client";

interface AgentEditState {
  agentId: string | null;
  agentName: string | null;
  formData: Partial<AgentMetadata> | null;
  originalData: Partial<AgentMetadata> | null;
  loading: boolean;
  saving: boolean;
  pendingSkillFiles: Record<string, Record<string, string>>;
  originalSkillFiles: Record<string, Record<string, string>>;
  editingSkillId: string | null;

  loadAgent: (agentId: string, agentName: string) => Promise<void>;
  openNewWithData: (data: Partial<AgentMetadata>) => void;
  updateField: <K extends keyof AgentMetadata>(key: K, value: AgentMetadata[K]) => void;
  setSaving: (saving: boolean) => void;
  hasChanges: () => boolean;
  markSaved: () => void;
  getChangedFields: () => Record<string, { old: string; new: string }>;
  addSkill: (entry: AgentSkillEntry) => void;
  removeSkill: (skillId: string) => void;
  updateSkillEntry: (skillId: string, updates: Partial<AgentSkillEntry>) => void;
  setPendingSkillFiles: (skillId: string, files: Record<string, string>) => void;
  initSkillFiles: (skillId: string, files: Record<string, string>) => void;
  getPendingSkillFiles: (skillId: string) => Record<string, string> | undefined;
  clearPendingSkillFiles: (skillId: string) => void;
  updatePendingSkillFile: (skillId: string, filePath: string, content: string) => void;
  setEditingSkillId: (skillId: string | null) => void;
}

/**
 * Inject missing built-in tool code from catalog.
 * For each tool in tool_names that has no @tool function in tool_definitions,
 * look it up in the catalog and append its code.
 */
async function injectBuiltinToolCode(data: Partial<AgentMetadata>): Promise<void> {
  const toolNames = (data.tool_names as string || "").split(",").map(t => t.trim()).filter(Boolean);
  if (!toolNames.length) return;

  const defs = data.tool_definitions || "";
  const definedFuncs = new Set([...(defs).matchAll(/@tool\s*\ndef\s+(\w+)\s*\(/g)].map(m => m[1]));

  const missing = toolNames.filter(n => !definedFuncs.has(n));
  if (!missing.length) return;

  try {
    const resp = await fetchTools(undefined, 100);
    const codeParts: string[] = [];
    for (const name of missing) {
      const tool = resp.items.find((t: any) => t.name === name);
      if (tool?.code) {
        codeParts.push(tool.code);
      }
    }
    if (codeParts.length) {
      const injected = codeParts.join("\n\n");
      data.tool_definitions = defs ? defs.trimEnd() + "\n\n" + injected : injected;
    }
  } catch (e) {
    console.warn("Failed to fetch tools:", e);
  }
}

export const useAgentEditStore = create<AgentEditState>((set, get) => ({
  agentId: null,
  agentName: null,
  formData: null,
  originalData: null,
  loading: false,
  saving: false,
  pendingSkillFiles: {},
  originalSkillFiles: {},
  editingSkillId: null,

  hasChanges: () => {
    const { formData, originalData, pendingSkillFiles, originalSkillFiles } = get();
    if (!formData || !originalData) return false;
    // Include skill files in change detection
    return JSON.stringify(formData) !== JSON.stringify(originalData) ||
           JSON.stringify(pendingSkillFiles) !== JSON.stringify(originalSkillFiles);
  },

  loadAgent: async (agentId, agentName) => {
    set({ agentId: agentId, agentName: agentName, loading: true, formData: null, pendingSkillFiles: {}, originalSkillFiles: {}, editingSkillId: null });

    // API 已经整合了 DDB + S3 数据，不需要 fallback
    let metadata = await fetchAgentMetadata(agentId);
    if (!metadata) {
      console.warn(`No metadata for ${agentId}`);
    }

    const data: Partial<AgentMetadata> = metadata || { name: agentName };
    if (!data.skills) data.skills = [];

    // Extract tools from deployment.zip if:
    // 1. No tool_definitions at all, OR
    // 2. tool_definitions has fewer @tool functions than the tools[] list (partial/stale metadata)
    const toolCount = (data.tool_definitions || "").split("@tool").length - 1;
    const expectedCount = (data.tools || []).length;
    if (!data.tool_definitions || (expectedCount > 0 && toolCount < expectedCount)) {
      if (agentId) {
        const extracted = await extractToolsFromDeployment(agentId);
        if (extracted) {
          data.tool_definitions = extracted.tool_definitions;
          if (!data.tool_names) data.tool_names = extracted.tool_names;
        }
      }
    }

    // Inject missing built-in tool code from catalog
    await injectBuiltinToolCode(data);

    set({ formData: data, originalData: JSON.parse(JSON.stringify(data)), loading: false });
  },

  openNewWithData: (data: Partial<AgentMetadata>) => {
    const draftId = `draft-${crypto.randomUUID().slice(0, 8)}`;
    if (!data.skills) data.skills = [];
    // Inject built-in tool code async, update formData when done
    set({
      agentId: draftId,
      agentName: (data.display_name || data.name || "New Agent") as string,
      formData: data,
      originalData: JSON.parse(JSON.stringify(data)),
      loading: false,
    });
    injectBuiltinToolCode(data).then(() => {
      set({ formData: { ...data }, originalData: JSON.parse(JSON.stringify(data)) });
    });
  },

  updateField: (key, value) => {
    const { formData } = get();
    if (formData) {
      set({ formData: { ...formData, [key]: value } });
    }
  },

  setSaving: (saving) => set({ saving }),

  markSaved: () => {
    const { formData, pendingSkillFiles } = get();
    if (formData) {
      set({
        originalData: JSON.parse(JSON.stringify(formData)),
        originalSkillFiles: JSON.parse(JSON.stringify(pendingSkillFiles)),
      });
    }
  },

  getChangedFields: () => {
    const { formData, originalData } = get();
    if (!formData || !originalData) return {};
    const changes: Record<string, { old: string; new: string }> = {};
    // Skip complex object fields that can't be meaningfully diffed as strings
    const skipKeys = new Set(["deployedSkillHashes", "tools", "updated_at", "created_at", "gateway_url"]);
    const keys = new Set([...Object.keys(formData), ...Object.keys(originalData)]);
    for (const key of keys) {
      if (skipKeys.has(key)) continue;
      if (key === "mcp_targets") {
        const oldTargets = ((originalData as Record<string, unknown>).mcp_targets as string[]) || [];
        const newTargets = ((formData as Record<string, unknown>).mcp_targets as string[]) || [];
        const oldVal = [...oldTargets].sort().join(", ");
        const newVal = [...newTargets].sort().join(", ");
        if (oldVal !== newVal) {
          changes[key] = { old: oldVal, new: newVal };
        }
        continue;
      }
      if (key === "skills") {
        // Per-file diff for each skill's changed files
        const { pendingSkillFiles: pending, originalSkillFiles: original } = get();
        const newSkills = ((formData as Record<string, unknown>).skills as Array<{ id: string; name: string; files: string[] }>) || [];
        const oldSkills = ((originalData as Record<string, unknown>).skills as Array<{ id: string; name: string; files: string[] }>) || [];
        const oldIds = new Set(oldSkills.map(s => s.id));

        for (const skill of newSkills) {
          const orig = original[skill.id] || {};
          const curr = pending[skill.id] || {};
          const allPaths = new Set([...Object.keys(orig), ...Object.keys(curr)]);
          for (const filePath of allPaths) {
            const oldContent = orig[filePath] ?? "";
            const newContent = curr[filePath] ?? "";
            if (oldContent !== newContent) {
              const prefix = !oldIds.has(skill.id) ? "+" : "";
              changes[`${prefix}${skill.name}/${filePath}`] = { old: oldContent, new: newContent };
            }
          }
          // If skill is newly added but no file edits yet, show SKILL.md as added
          if (!oldIds.has(skill.id) && Object.keys(orig).length > 0 && Object.keys(changes).filter(k => k.startsWith(`+${skill.name}/`)).length === 0) {
            const md = curr["SKILL.md"] || orig["SKILL.md"] || "";
            if (md) changes[`+${skill.name}/SKILL.md`] = { old: "", new: md };
          }
        }
        continue;
      }
      if (key === "suggestions") {
        // Serialize suggestions as readable list
        const oldSug = ((originalData as Record<string, unknown>).suggestions as string[]) || [];
        const newSug = ((formData as Record<string, unknown>).suggestions as string[]) || [];
        const oldVal = Array.isArray(oldSug) ? oldSug.join("\n") : String(oldSug);
        const newVal = Array.isArray(newSug) ? newSug.join("\n") : String(newSug);
        if (oldVal !== newVal) {
          changes[key] = { old: oldVal, new: newVal };
        }
        continue;
      }
      const oldVal = String((originalData as Record<string, unknown>)[key] ?? "");
      const newVal = String((formData as Record<string, unknown>)[key] ?? "");
      if (oldVal !== newVal) {
        changes[key] = { old: oldVal, new: newVal };
      }
    }
    return changes;
  },

  addSkill: (entry: AgentSkillEntry) => {
    const { formData } = get();
    if (!formData) return;
    const skills = [...(formData.skills || []), entry];
    set({ formData: { ...formData, skills } });
  },

  removeSkill: (skillId: string) => {
    const { formData, pendingSkillFiles, originalSkillFiles, agentId } = get();
    if (!formData) return;
    const skills = (formData.skills || []).filter(s => s.id !== skillId);
    const nextPending = { ...pendingSkillFiles };
    delete nextPending[skillId];
    const nextOriginal = { ...originalSkillFiles };
    delete nextOriginal[skillId];
    set({ formData: { ...formData, skills }, pendingSkillFiles: nextPending, originalSkillFiles: nextOriginal });
    // Clean up S3 files for existing agents (not drafts)
    if (agentId && !agentId.startsWith("draft-")) {
      deleteAgentSkillFiles(agentId, skillId).catch(() => {});
    }
  },

  updateSkillEntry: (skillId: string, updates: Partial<AgentSkillEntry>) => {
    const { formData } = get();
    if (!formData) return;
    const skills = (formData.skills || []).map(s =>
      s.id === skillId ? { ...s, ...updates } : s
    );
    set({ formData: { ...formData, skills } });
  },

  setPendingSkillFiles: (skillId, files) => {
    const { pendingSkillFiles, originalSkillFiles } = get();
    set({
      pendingSkillFiles: { ...pendingSkillFiles, [skillId]: files },
      // Set original to same files if not already present — this is the baseline for diff
      originalSkillFiles: originalSkillFiles[skillId]
        ? originalSkillFiles
        : { ...originalSkillFiles, [skillId]: { ...files } },
    });
  },

  initSkillFiles: (skillId, files) => {
    const { originalSkillFiles } = get();
    // Don't overwrite if already initialized (e.g. by setPendingSkillFiles when skill was added)
    if (originalSkillFiles[skillId] && Object.keys(originalSkillFiles[skillId]).length > 0) return;
    set({
      originalSkillFiles: { ...originalSkillFiles, [skillId]: { ...files } },
    });
  },

  getPendingSkillFiles: (skillId) => {
    return get().pendingSkillFiles[skillId];
  },

  clearPendingSkillFiles: (skillId) => {
    const { pendingSkillFiles, originalSkillFiles } = get();
    const nextPending = { ...pendingSkillFiles };
    delete nextPending[skillId];
    const nextOriginal = { ...originalSkillFiles };
    delete nextOriginal[skillId];
    set({ pendingSkillFiles: nextPending, originalSkillFiles: nextOriginal });
  },

  updatePendingSkillFile: (skillId, filePath, content) => {
    const { pendingSkillFiles } = get();
    const current = pendingSkillFiles[skillId] || {};
    set({ pendingSkillFiles: { ...pendingSkillFiles, [skillId]: { ...current, [filePath]: content } } });
  },

  setEditingSkillId: (skillId) => set({ editingSkillId: skillId }),
}));
