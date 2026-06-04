import { create } from "zustand";
import { fetchAgentMetadata, type AgentMetadata, type AgentSkillEntry } from "../lib/agent-metadata";
import { extractToolsFromDeployment } from "../lib/tool-extractor";
import { fetchTools, deleteAgentSkillFiles, fetchAgentSkillFilesBulk } from "../lib/api-client";
import { createDebouncedSaver, loadDraftWithMeta, clearDraft } from "../lib/draft-autosave";

/**
 * Local draft autosave for agent edits. Survives F5 and workspace
 * switches (drafts are keyed by agentId, which is workspace-scoped on
 * the server; the new workspace simply can't address the old id, so
 * there's nothing to leak and everything to preserve if the user
 * round-trips back).
 *
 * Does NOT survive sign-out or a different user logging in on the
 * same browser — api-client.clearUserScopedLocalData wipes everything
 * then.
 */
function draftKeyFor(agentId: string): string {
  return `agent-draft:${agentId}`;
}

interface AgentDraft {
  formData: Partial<AgentMetadata>;
  pendingSkillFiles: Record<string, Record<string, string>>;
  // Snapshot of originalData at the time the draft was captured. Needed so
  // the reloaded store can still compute "is dirty" — without this, we'd
  // treat every restored field as a change against a server-fresh baseline.
  originalData: Partial<AgentMetadata>;
  originalSkillFiles: Record<string, Record<string, string>>;
}

// Keyed by agentId so switching between agents doesn't cross-contaminate.
const _draftSavers = new Map<string, ReturnType<typeof createDebouncedSaver<AgentDraft>>>();

function _getSaver(agentId: string) {
  let saver = _draftSavers.get(agentId);
  if (!saver) {
    saver = createDebouncedSaver<AgentDraft>(draftKeyFor(agentId), 750);
    _draftSavers.set(agentId, saver);
  }
  return saver;
}

function _deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

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
  // Information about a draft just restored from localStorage. AgentEditForm
  // reads this in an effect to fire a "Restored from Xm ago" toast and
  // then resets it via clearRestoredNotice. Bound to agentId so a rapid
  // A→B switch can't fire A's toast while B is on screen.
  restoredDraft: { agentId: string; ts: number } | null;

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
  clearRestoredNotice: () => void;
}

/**
 * Inject missing built-in tool code from catalog.
 * For each tool in tool_names that has no @tool function in tool_definitions,
 * look it up in the catalog and append its code.
 */
async function injectBuiltinToolCode(data: Partial<AgentMetadata>): Promise<void> {
  const toolNames = (data.tool_names! || "").split(",").map(t => t.trim()).filter(Boolean);
  if (!toolNames.length) return;

  const defs = data.tool_definitions || "";
  const definedFuncs = new Set([...(defs).matchAll(/@tool\s*\ndef\s+(\w+)\s*\(/g)].map(m => m[1]));

  const missing = toolNames.filter(n => !definedFuncs.has(n));
  if (!missing.length) return;

  try {
    const resp = await fetchTools(undefined, 100);
    const codeParts: string[] = [];
    for (const name of missing) {
      // tool_names holds function names (e.g. "web_search") — the ToolItem's
      // `toolId` is the function name, `name` is the display label ("Web
      // Search"). Match on toolId; fall back to name for hand-authored cases.
      const tool = resp.items.find((t: any) => t.toolId === name)
        ?? resp.items.find((t: any) => t.name === name);
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
  restoredDraft: null,

  clearRestoredNotice: () => set({ restoredDraft: null }),

  hasChanges: () => {
    const { formData, originalData, pendingSkillFiles, originalSkillFiles } = get();
    if (!formData || !originalData) return false;
    // Use getChangedFields for consistency — if diff shows nothing, no unsaved changes
    const fieldChanges = get().getChangedFields();
    if (Object.keys(fieldChanges).length > 0) return true;
    // Also check skill file changes
    return JSON.stringify(pendingSkillFiles) !== JSON.stringify(originalSkillFiles);
  },

  loadAgent: async (agentId, agentName) => {
    set({ agentId: agentId, agentName: agentName, loading: true, formData: null, pendingSkillFiles: {}, originalSkillFiles: {}, editingSkillId: null, restoredDraft: null });

    // API 已经整合了 DDB + S3 数据，不需要 fallback
    const metadata = await fetchAgentMetadata(agentId);
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

    // Look for a local draft. Restore the user's in-progress form + skill
    // file edits on top of the *fresh* server baseline — not the snapshot
    // that was captured alongside the draft. If another user (or this same
    // user from another device) changed the server between draft capture
    // and now, those remote edits stay in `originalData`; the dirty check
    // and diff view compare against current server state, and a subsequent
    // save won't silently overwrite fields the draft never touched.
    //
    // The draft's stored `originalData` is deliberately discarded. Keeping
    // it would mean "save as-of-3-days-ago" semantics, which silently
    // clobbers newer concurrent edits.
    const meta = loadDraftWithMeta<AgentDraft>(draftKeyFor(agentId));
    if (meta && meta.data.formData) {
      const freshOriginal: Partial<AgentMetadata> = JSON.parse(JSON.stringify(data));
      const pending = meta.data.pendingSkillFiles || {};
      // Eagerly fetch current server copies for every skill that has
      // pending edits, so `originalSkillFiles` is an accurate baseline
      // for the dirty check AND the diff view. Without this the user
      // sees every pending file as "added from empty" and a subsequent
      // save would blindly push the full draft content, overwriting any
      // concurrent server-side changes the user didn't touch.
      //
      // Runs in parallel and tolerates per-skill failures — a missing
      // fetch just leaves that skill's baseline empty, which is the same
      // as the pre-fix behaviour.
      const skillIds = Object.keys(pending);
      const freshOriginalFiles: Record<string, Record<string, string>> = {};
      if (skillIds.length > 0 && !agentId.startsWith("draft-")) {
        const results = await Promise.all(
          skillIds.map((sid) =>
            fetchAgentSkillFilesBulk(agentId, sid)
              .then((files) => [sid, files] as const)
              .catch(() => [sid, {}] as const),
          ),
        );
        for (const [sid, files] of results) freshOriginalFiles[sid] = files;
      }
      set({
        formData: meta.data.formData,
        originalData: freshOriginal,
        pendingSkillFiles: pending,
        originalSkillFiles: freshOriginalFiles,
        loading: false,
        restoredDraft: { agentId, ts: meta.ts },
      });
      return;
    }

    set({ formData: data, originalData: JSON.parse(JSON.stringify(data)), loading: false, restoredDraft: null });
  },

  openNewWithData: (data: Partial<AgentMetadata>) => {
    const draftId = `draft-${crypto.randomUUID().slice(0, 8)}`;
    if (!data.skills) data.skills = [];
    // Inject built-in tool code async, update formData when done
    set({
      agentId: draftId,
      agentName: (data.display_name || data.name || "New Agent"),
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
    const { agentId, formData, pendingSkillFiles } = get();
    if (formData) {
      set({
        originalData: JSON.parse(JSON.stringify(formData)),
        originalSkillFiles: JSON.parse(JSON.stringify(pendingSkillFiles)),
      });
    }
    // After a successful save the draft is obsolete — drop it so a reload
    // doesn't resurrect the diff against the new server baseline.
    if (agentId) {
      const saver = _draftSavers.get(agentId);
      saver?.cancel();
      clearDraft(draftKeyFor(agentId));
    }
  },

  getChangedFields: () => {
    const { formData, originalData } = get();
    if (!formData || !originalData) return {};
    const changes: Record<string, { old: string; new: string }> = {};
    // Skip complex object fields that can't be meaningfully diffed as strings
    const skipKeys = new Set(["deployedSkillHashes", "tools", "updated_at", "created_at", "gateway_url", "memory"]);
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
        const newSkills = ((formData as Record<string, unknown>).skills as { id: string; name: string; files: string[] }[]) || [];
        const oldSkills = ((originalData as Record<string, unknown>).skills as { id: string; name: string; files: string[] }[]) || [];
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

// ── Draft autosave wiring ───────────────────────────────────────────────
//
// Subscribe to store changes and debounce-write the draft to localStorage
// whenever the user has unsaved work. Pristine state (no diff vs originals)
// wipes any stored draft so we don't replay a fake edit on next load.
//
// Lives at module scope rather than inside the create factory so there's
// exactly one listener regardless of how many components mount.

useAgentEditStore.subscribe((state, prev) => {
  const { agentId, formData, originalData, pendingSkillFiles, originalSkillFiles, loading } = state;
  if (!agentId || !formData || !originalData || loading) return;
  // Skip spurious no-op updates (the loading=false tail of loadAgent, etc.)
  if (
    prev?.formData === formData &&
    prev.pendingSkillFiles === pendingSkillFiles &&
    prev.originalData === originalData &&
    prev.originalSkillFiles === originalSkillFiles
  ) {
    return;
  }
  const formDirty = !_deepEqual(formData, originalData);
  const filesDirty = !_deepEqual(pendingSkillFiles, originalSkillFiles);
  const saver = _getSaver(agentId);
  if (formDirty || filesDirty) {
    saver.schedule({ formData, originalData, pendingSkillFiles, originalSkillFiles });
  } else {
    saver.cancel();
    clearDraft(draftKeyFor(agentId));
  }
});

/** Cancel every pending saver. Used on workspace switch before wiping keys. */
export function cancelAllAgentDraftSavers(): void {
  for (const saver of _draftSavers.values()) saver.cancel();
  _draftSavers.clear();
}

// Flush every pending agent-draft save on tab-close / reload / backgrounding
// so the last 750 ms of typing isn't dropped on the floor.
if (typeof window !== "undefined") {
  const flushAll = () => {
    for (const saver of _draftSavers.values()) saver.flush();
  };
  window.addEventListener("beforeunload", flushAll);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushAll();
  });
}
