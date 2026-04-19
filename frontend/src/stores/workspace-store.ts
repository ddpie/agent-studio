import { create } from "zustand";
import { apiGetRaw, getWorkspaceId } from "../lib/api-client";

export type WorkspaceRole = "viewer" | "editor" | "admin" | "owner";

export interface WorkspaceSummary {
  workspaceId: string;
  name?: string;
  description?: string;
  role: WorkspaceRole;
}

interface WorkspaceState {
  currentWorkspace: WorkspaceSummary | null;
  loading: boolean;
  loaded: boolean;
  loadCurrentWorkspace: () => Promise<void>;
}

function canonicaliseRole(role: string | undefined): WorkspaceRole {
  if (role === "editor" || role === "admin" || role === "owner") return role;
  return "viewer";
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  currentWorkspace: null,
  loading: false,
  loaded: false,
  loadCurrentWorkspace: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const wsId = getWorkspaceId();
      const resp = await apiGetRaw<{
        items?: Array<{ workspaceId: string; name?: string; description?: string; role?: string }>;
      }>("/api/workspaces");
      const match = resp.items?.find((w) => w.workspaceId === wsId) || resp.items?.[0];
      if (match) {
        set({
          currentWorkspace: {
            workspaceId: match.workspaceId,
            name: match.name,
            description: match.description,
            role: canonicaliseRole(match.role),
          },
          loaded: true,
        });
      } else {
        set({ currentWorkspace: null, loaded: true });
      }
    } catch {
      set({ loaded: true });
    } finally {
      set({ loading: false });
    }
  },
}));
