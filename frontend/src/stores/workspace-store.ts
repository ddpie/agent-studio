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
  owner_id?: string;
  owner_name?: string;
  owner_email?: string;
}

interface WorkspaceState {
  currentWorkspace: WorkspaceSummary | null;
  workspaces: WorkspaceSummary[];
  loading: boolean;
  loaded: boolean;
  loadCurrentWorkspace: () => Promise<void>;
  refreshWorkspaces: () => Promise<void>;
  switchWorkspace: (wsId: string) => Promise<void>;
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
  owner_id?: string;
  owner_name?: string;
  owner_email?: string;
}): WorkspaceSummary {
  return {
    workspaceId: raw.workspaceId,
    name: raw.name,
    description: raw.description,
    role: canonicaliseRole(raw.role),
    created_at: raw.created_at,
    owner_id: raw.owner_id,
    owner_name: raw.owner_name,
    owner_email: raw.owner_email,
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
  switchWorkspace: async (wsId: string) => {
    const target = get().workspaces.find((w) => w.workspaceId === wsId);
    if (!target) return;
    // setWorkspaceId schedules a fire-and-forget async clear of the chat
    // store's localStorage (it dynamic-imports chat-store to avoid a static
    // cycle). We must await that microtask before reloading, otherwise the
    // reload races ahead and the previous workspace's chat history survives
    // into the new workspace.
    await setWorkspaceId(wsId);
    // Demote workspace-scoped detail routes (e.g. /agents/edit/<id>) to their
    // list-view ancestor so we don't land on a 404 in the target ws. Set the
    // hash first, then force a full reload — assigning the same URL is a
    // no-op, so we can't rely on location.assign() alone.
    const nextHash = demoteHashForWorkspaceSwitch(window.location.hash);
    if (nextHash !== window.location.hash) {
      window.location.hash = nextHash;
    }
    window.location.reload();
  },
}));
