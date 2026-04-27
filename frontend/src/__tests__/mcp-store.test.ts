import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useMcpStore } from "../stores/mcp-store";
import * as api from "../lib/api-client";

// Mock the two api-client functions the store calls.
vi.mock("../lib/api-client", async (orig) => {
  const actual = await orig<typeof api>();
  return {
    ...actual,
    getMcpCatalog: vi.fn(),
    getMcpStatus: vi.fn(),
  };
});

describe("mcp-store", () => {
  beforeEach(() => {
    // Reset store state
    useMcpStore.setState({
      workspaceId: null,
      catalog: null,
      loading: false,
      error: null,
      polls: {},
    });
    vi.useFakeTimers();
  });

  afterEach(() => {
    // stop all timers before restoring real timers
    useMcpStore.getState().stopAllPolls();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("setWorkspace clears state when workspace changes", () => {
    const store = useMcpStore.getState();
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: {
        workspaceRoleExists: true,
        targets: [
          {
            name: "cloudwatch",
            displayName: "cloudwatch",
            description: "",
            category: "observability",
            sensitivity: "low",
            sensitiveReasons: [],
            latestVersion: "0.0.24",
            enabled: true,
            runtime: null,
          },
        ],
      },
    });
    store.setWorkspace("ws-2");
    const next = useMcpStore.getState();
    expect(next.workspaceId).toBe("ws-2");
    expect(next.catalog).toBeNull();
  });

  it("setWorkspace is a no-op when same id", () => {
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: { workspaceRoleExists: true, targets: [] },
    });
    useMcpStore.getState().setWorkspace("ws-1");
    expect(useMcpStore.getState().catalog).not.toBeNull();
  });

  it("refreshCatalog loads and stores catalog", async () => {
    (api.getMcpCatalog as ReturnType<typeof vi.fn>).mockResolvedValue({
      workspaceRoleExists: true,
      targets: [],
    });
    useMcpStore.getState().setWorkspace("ws-1");
    await useMcpStore.getState().refreshCatalog();
    const s = useMcpStore.getState();
    expect(s.catalog).not.toBeNull();
    expect(s.loading).toBe(false);
    expect(s.error).toBeNull();
  });

  it("refreshCatalog records error on failure", async () => {
    (api.getMcpCatalog as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("network boom"),
    );
    useMcpStore.getState().setWorkspace("ws-1");
    await useMcpStore.getState().refreshCatalog();
    expect(useMcpStore.getState().error).toBe("network boom");
    expect(useMcpStore.getState().loading).toBe(false);
  });

  it("startPoll schedules and stops on terminal status", async () => {
    (api.getMcpStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: "READY",
      runtime_id: "rt-xxx",
    });
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: {
        workspaceRoleExists: true,
        targets: [
          {
            name: "cloudwatch",
            displayName: "cloudwatch",
            description: "",
            category: "observability",
            sensitivity: "low",
            sensitiveReasons: [],
            latestVersion: "0.0.24",
            enabled: true,
            runtime: { status: "CREATING" },
          },
        ],
      },
    });
    useMcpStore.getState().startPoll("cloudwatch");
    expect(useMcpStore.getState().polls["cloudwatch"]).toBeDefined();

    // Advance first scheduled delay (5s) + flush micro-tasks
    await vi.advanceTimersByTimeAsync(5_000);

    // After READY, poll should be cleared and catalog entry updated
    const s = useMcpStore.getState();
    expect(s.polls["cloudwatch"]).toBeUndefined();
    const t = s.catalog?.targets.find((x) => x.name === "cloudwatch");
    expect(t?.runtime?.status).toBe("READY");
  });

  it("startPoll cancels an existing timer for the same target", () => {
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: { workspaceRoleExists: true, targets: [] },
    });
    const store = useMcpStore.getState();
    store.startPoll("cloudwatch");
    const first = useMcpStore.getState().polls["cloudwatch"];
    expect(first).toBeDefined();
    store.startPoll("cloudwatch");
    const second = useMcpStore.getState().polls["cloudwatch"];
    expect(second).toBeDefined();
    expect(second).not.toBe(first);
  });

  it("stopAllPolls clears every timer", () => {
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: { workspaceRoleExists: true, targets: [] },
    });
    const store = useMcpStore.getState();
    store.startPoll("cloudwatch");
    store.startPoll("iam");
    expect(Object.keys(useMcpStore.getState().polls).length).toBe(2);
    store.stopAllPolls();
    expect(useMcpStore.getState().polls).toEqual({});
  });

  it("updateTargetEntry patches the catalog in place", () => {
    useMcpStore.setState({
      workspaceId: "ws-1",
      catalog: {
        workspaceRoleExists: true,
        targets: [
          {
            name: "cloudwatch",
            displayName: "cloudwatch",
            description: "",
            category: "observability",
            sensitivity: "low",
            sensitiveReasons: [],
            latestVersion: "0.0.24",
            enabled: false,
            runtime: null,
          },
        ],
      },
    });
    useMcpStore.getState().updateTargetEntry("cloudwatch", {
      status: "READY",
      runtime_id: "rt-xx",
    });
    const t = useMcpStore
      .getState()
      .catalog?.targets.find((x) => x.name === "cloudwatch");
    expect(t?.enabled).toBe(true);
    expect(t?.runtime?.status).toBe("READY");
  });
});
