import { create } from "zustand";
import {
  fetchWorkspaces,
  getWorkspaceId,
  setWorkspaceId,
} from "../lib/api-client";
import { demoteHashForWorkspaceSwitch } from "../lib/workspace-switch-url";

export type WorkspaceRole = "viewer" | "editor" | "admin" | "owner";

export interface WorkspaceSummary {
  workspaceId: string;
  name?: string;
  description?: string;
  role: WorkspaceRole;
  created_at?: string;
}

interface WorkspaceState {
  currentWorkspace: WorkspaceSummary | null;
  workspaces: WorkspaceSummary[];
  loading: boolean;
  loaded: boolean;
  loadCurrentWorkspace: () => Promise<void>;
  refreshWorkspaces: () => Promise<void>;
  switchWorkspace: (wsId: string) => void;
}

function canonicaliseRole(role: string | undefined): WorkspaceRole {
  if (role === "editor" || role === "admin" || role === "owner") return role;
  return "viewer";
}

function toSummary(raw: {
  workspaceId: string;
  name?: string;
  description?: string;
  role?: string;
  created_at?: string;
}): WorkspaceSummary {
  return {
    workspaceId: raw.workspaceId,
    name: raw.name,
    description: raw.description,
    role: canonicaliseRole(raw.role),
    created_at: raw.created_at,
  };
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  currentWorkspace: null,
  workspaces: [],
  loading: false,
  loaded: false,
  loadCurrentWorkspace: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const wsId = getWorkspaceId();
      const resp = await fetchWorkspaces();
      const items = (resp.items ?? []).map(toSummary);
      const match = items.find((w) => w.workspaceId === wsId) || items[0] || null;
      if (match) {
        setWorkspaceId(match.workspaceId);
      }
      set({
        workspaces: items,
        currentWorkspace: match,
        loaded: true,
      });
    } catch {
      set({ loaded: true });
    } finally {
      set({ loading: false });
    }
  },
  refreshWorkspaces: async () => {
    try {
      const resp = await fetchWorkspaces();
      const items = (resp.items ?? []).map(toSummary);
      const currentId = get().currentWorkspace?.workspaceId || getWorkspaceId();
      const match = items.find((w) => w.workspaceId === currentId) || items[0] || null;
      set({
        workspaces: items,
        currentWorkspace: match,
      });
      if (match) setWorkspaceId(match.workspaceId);
    } catch {
      // ignore — keep existing state
    }
  },
  switchWorkspace: (wsId: string) => {
    const target = get().workspaces.find((w) => w.workspaceId === wsId);
    if (!target) return;
    setWorkspaceId(wsId);
    // Hard reload so per-workspace state (agent list, sessions, stores) resets
    // cleanly. Preserve the current route but demote any workspace-scoped
    // detail page (e.g. /agents/edit/<id>) to its list-view ancestor so we
    // don't land on a 404 for a resource that doesn't exist in the target ws.
    const nextHash = demoteHashForWorkspaceSwitch(window.location.hash);
    window.location.assign(
      window.location.pathname + window.location.search + nextHash,
    );
  },
}));
