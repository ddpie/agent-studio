import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("aws-amplify/auth", () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: { idToken: { toString: () => "mock-token" } },
  }),
}));

vi.mock("../../lib/api-client", () => ({
  getWorkspaceId: vi.fn(() => "ws-test"),
  fetchAgents: vi.fn(),
}));

import { useAgentListStore } from "../agent-list-store";
import { fetchAgents } from "../../lib/api-client";

const mockFetchAgents = vi.mocked(fetchAgents);

beforeEach(() => {
  vi.clearAllMocks();
  useAgentListStore.setState({ agents: [], archivedAgents: [], loading: false });
});

describe("agent-list-store", () => {
  it("fetchAgents 从 API 加载 agent 列表", async () => {
    mockFetchAgents.mockResolvedValue({
      items: [
        { agentId: "a-1", name: "bot1", description: "desc1", status: "active", display_name: "Bot 1" },
        { agentId: "a-2", name: "bot2", description: "desc2", status: "archived", display_name: "Bot 2" },
      ],
    });

    await useAgentListStore.getState().fetchAgents();
    const state = useAgentListStore.getState();

    expect(mockFetchAgents).toHaveBeenCalled();
    expect(state.agents).toHaveLength(1);
    expect(state.agents[0].id).toBe("a-1");
    expect(state.archivedAgents).toHaveLength(1);
    expect(state.archivedAgents[0].id).toBe("a-2");
    expect(state.loading).toBe(false);
  });

  it("API 失败时清空列表", async () => {
    mockFetchAgents.mockRejectedValue(new Error("Network error"));

    await useAgentListStore.getState().fetchAgents();
    const state = useAgentListStore.getState();

    expect(state.agents).toEqual([]);
    expect(state.loading).toBe(false);
  });
});
