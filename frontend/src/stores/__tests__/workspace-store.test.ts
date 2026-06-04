import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../../lib/api-client", () => ({
  fetchWorkspaces: vi.fn(),
  getWorkspaceId: vi.fn(),
  setWorkspaceId: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../../lib/workspace-switch-url", () => ({
  // Identity by default — switchWorkspace tests will spy on it.
  demoteHashForWorkspaceSwitch: vi.fn((hash: string) => hash),
}));

import { useWorkspaceStore } from "../workspace-store";
import {
  fetchWorkspaces,
  getWorkspaceId,
  setWorkspaceId,
} from "../../lib/api-client";
import { demoteHashForWorkspaceSwitch } from "../../lib/workspace-switch-url";

const mockFetchWorkspaces = vi.mocked(fetchWorkspaces);
const mockGetWorkspaceId = vi.mocked(getWorkspaceId);
const mockSetWorkspaceId = vi.mocked(setWorkspaceId);
const mockDemote = vi.mocked(demoteHashForWorkspaceSwitch);

const initialState = {
  currentWorkspace: null,
  workspaces: [],
  loading: false,
  loaded: false,
};

// jsdom's location.reload is a no-op fn but throws "Not implemented" in some
// node versions; stub it. We also need to control window.location.hash.
let reloadSpy: ReturnType<typeof vi.fn>;
let originalLocation: Location;

beforeEach(() => {
  vi.clearAllMocks();
  // Merge-mode setState (no replace flag) so the action closures defined at
  // store creation time survive between tests.
  useWorkspaceStore.setState(initialState);
  reloadSpy = vi.fn();
  originalLocation = window.location;
  // Replace location with a configurable surrogate so we can assert hash and reload.
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: {
      ...originalLocation,
      hash: "",
      reload: reloadSpy,
    },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    writable: true,
    value: originalLocation,
  });
});

describe("workspace-store", () => {
  describe("loadCurrentWorkspace", () => {
    it("populates workspaces and selects the matching workspace by id", async () => {
      mockGetWorkspaceId.mockReturnValue("ws-2");
      mockFetchWorkspaces.mockResolvedValue({
        items: [
          { workspaceId: "ws-1", name: "Alpha", role: "viewer" },
          { workspaceId: "ws-2", name: "Beta", role: "admin" },
        ],
      });

      await useWorkspaceStore.getState().loadCurrentWorkspace();
      const s = useWorkspaceStore.getState();

      expect(s.workspaces).toHaveLength(2);
      expect(s.currentWorkspace?.workspaceId).toBe("ws-2");
      expect(s.currentWorkspace?.role).toBe("admin");
      expect(s.loaded).toBe(true);
      expect(s.loading).toBe(false);
      expect(mockSetWorkspaceId).toHaveBeenCalledWith("ws-2");
    });

    it("falls back to the first workspace when active id is unknown", async () => {
      mockGetWorkspaceId.mockReturnValue("missing");
      mockFetchWorkspaces.mockResolvedValue({
        items: [{ workspaceId: "ws-1", role: "owner" }],
      });

      await useWorkspaceStore.getState().loadCurrentWorkspace();
      const s = useWorkspaceStore.getState();

      expect(s.currentWorkspace?.workspaceId).toBe("ws-1");
      expect(s.currentWorkspace?.role).toBe("owner");
      expect(mockSetWorkspaceId).toHaveBeenCalledWith("ws-1");
    });

    it("canonicalises unknown roles down to viewer", async () => {
      mockGetWorkspaceId.mockReturnValue("ws-1");
      mockFetchWorkspaces.mockResolvedValue({
        items: [{ workspaceId: "ws-1", role: "weird-role" }],
      });

      await useWorkspaceStore.getState().loadCurrentWorkspace();
      expect(useWorkspaceStore.getState().currentWorkspace?.role).toBe("viewer");
    });

    it("handles an empty list with currentWorkspace=null", async () => {
      mockGetWorkspaceId.mockReturnValue("anything");
      mockFetchWorkspaces.mockResolvedValue({ items: [] });

      await useWorkspaceStore.getState().loadCurrentWorkspace();
      const s = useWorkspaceStore.getState();

      expect(s.workspaces).toEqual([]);
      expect(s.currentWorkspace).toBeNull();
      expect(s.loaded).toBe(true);
      expect(mockSetWorkspaceId).not.toHaveBeenCalled();
    });

    it("on API failure marks loaded=true and clears loading", async () => {
      mockGetWorkspaceId.mockReturnValue("ws-1");
      mockFetchWorkspaces.mockRejectedValue(new Error("boom"));

      await useWorkspaceStore.getState().loadCurrentWorkspace();
      const s = useWorkspaceStore.getState();

      expect(s.loaded).toBe(true);
      expect(s.loading).toBe(false);
    });

    it("is a no-op when already loading", async () => {
      useWorkspaceStore.setState({ loading: true });
      await useWorkspaceStore.getState().loadCurrentWorkspace();
      expect(mockFetchWorkspaces).not.toHaveBeenCalled();
    });
  });

  describe("refreshWorkspaces", () => {
    it("refreshes the list and re-selects the current workspace", async () => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-1",
          role: "editor",
        },
      });
      mockFetchWorkspaces.mockResolvedValue({
        items: [
          { workspaceId: "ws-1", name: "Renamed", role: "admin" },
          { workspaceId: "ws-2", role: "viewer" },
        ],
      });

      await useWorkspaceStore.getState().refreshWorkspaces();
      const s = useWorkspaceStore.getState();
      expect(s.workspaces).toHaveLength(2);
      expect(s.currentWorkspace?.workspaceId).toBe("ws-1");
      expect(s.currentWorkspace?.name).toBe("Renamed");
      expect(s.currentWorkspace?.role).toBe("admin");
      expect(mockSetWorkspaceId).toHaveBeenCalledWith("ws-1");
    });

    it("falls back to first item when current id is gone", async () => {
      useWorkspaceStore.setState({
        currentWorkspace: { workspaceId: "stale", role: "viewer" },
      });
      mockFetchWorkspaces.mockResolvedValue({
        items: [{ workspaceId: "ws-9", role: "owner" }],
      });

      await useWorkspaceStore.getState().refreshWorkspaces();
      expect(useWorkspaceStore.getState().currentWorkspace?.workspaceId).toBe("ws-9");
    });

    it("on failure keeps existing state", async () => {
      const before = {
        workspaceId: "ws-keep",
        role: "owner" as const,
      };
      useWorkspaceStore.setState({
        currentWorkspace: before,
        workspaces: [before],
      });
      mockFetchWorkspaces.mockRejectedValue(new Error("net"));

      await useWorkspaceStore.getState().refreshWorkspaces();
      expect(useWorkspaceStore.getState().currentWorkspace).toEqual(before);
    });
  });

  describe("switchWorkspace", () => {
    it("no-ops when target workspace is not in the list", async () => {
      useWorkspaceStore.setState({
        workspaces: [{ workspaceId: "ws-1", role: "viewer" }],
      });
      await useWorkspaceStore.getState().switchWorkspace("ws-other");
      expect(mockSetWorkspaceId).not.toHaveBeenCalled();
      expect(reloadSpy).not.toHaveBeenCalled();
    });

    it("setWorkspaceId, demotes the hash if needed, then reloads", async () => {
      useWorkspaceStore.setState({
        workspaces: [
          { workspaceId: "ws-1", role: "viewer" },
          { workspaceId: "ws-2", role: "admin" },
        ],
      });
      window.location.hash = "#/agents/edit/abc";
      mockDemote.mockReturnValueOnce("#/agents");

      await useWorkspaceStore.getState().switchWorkspace("ws-2");

      expect(mockSetWorkspaceId).toHaveBeenCalledWith("ws-2");
      expect(mockDemote).toHaveBeenCalledWith("#/agents/edit/abc");
      expect(window.location.hash).toBe("#/agents");
      expect(reloadSpy).toHaveBeenCalled();
    });

    it("does not re-write hash when demote returns the same value", async () => {
      useWorkspaceStore.setState({
        workspaces: [{ workspaceId: "ws-2", role: "admin" }],
      });
      window.location.hash = "#/marketplace";
      mockDemote.mockReturnValueOnce("#/marketplace");

      await useWorkspaceStore.getState().switchWorkspace("ws-2");
      expect(window.location.hash).toBe("#/marketplace");
      expect(reloadSpy).toHaveBeenCalled();
    });
  });
});
