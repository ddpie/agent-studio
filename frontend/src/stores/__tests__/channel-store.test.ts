import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api-client", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiPut: vi.fn(),
  apiDelete: vi.fn(),
}));

import { useChannelStore, type Channel } from "../channel-store";
import { apiGet, apiPost, apiPut, apiDelete } from "../../lib/api-client";

const mockGet = vi.mocked(apiGet);
const mockPost = vi.mocked(apiPost);
const mockPut = vi.mocked(apiPut);
const mockDelete = vi.mocked(apiDelete);

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    channelId: "ch-1",
    channelType: "feishu",
    channelName: "Feishu Bot",
    status: "active",
    defaultAgentId: "agent-1",
    routingRules: [],
    triggerMode: "mention",
    platformConfig: {},
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useChannelStore.setState({ channels: [], loading: false, error: null });
});

describe("channel-store", () => {
  it("fetchChannels 加载频道列表并清除错误", async () => {
    const ch1 = makeChannel({ channelId: "ch-1" });
    const ch2 = makeChannel({ channelId: "ch-2", channelType: "slack" });
    mockGet.mockResolvedValue({ channels: [ch1, ch2] });

    await useChannelStore.getState().fetchChannels();
    const state = useChannelStore.getState();

    expect(mockGet).toHaveBeenCalledWith("/channels");
    expect(state.channels).toHaveLength(2);
    expect(state.channels[0].channelId).toBe("ch-1");
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("fetchChannels 在 channels 缺失时回退为空数组", async () => {
    mockGet.mockResolvedValue({});

    await useChannelStore.getState().fetchChannels();
    const state = useChannelStore.getState();

    expect(state.channels).toEqual([]);
    expect(state.loading).toBe(false);
  });

  it("fetchChannels 失败时记录错误信息", async () => {
    mockGet.mockRejectedValue(new Error("network down"));

    await useChannelStore.getState().fetchChannels();
    const state = useChannelStore.getState();

    expect(state.error).toBe("network down");
    expect(state.loading).toBe(false);
    expect(state.channels).toEqual([]);
  });

  it("fetchChannels 处理非 Error rejection 使用默认错误信息", async () => {
    mockGet.mockRejectedValue("oops");

    await useChannelStore.getState().fetchChannels();
    const state = useChannelStore.getState();

    expect(state.error).toBe("Failed to fetch channels");
  });

  it("createChannel 调用 POST 并把新频道追加到列表", async () => {
    const created = makeChannel({ channelId: "ch-new" });
    mockPost.mockResolvedValue(created);

    useChannelStore.setState({
      channels: [makeChannel({ channelId: "ch-existing" })],
      loading: false,
      error: null,
    });

    const result = await useChannelStore.getState().createChannel({
      channelName: "New Bot",
      channelType: "feishu",
      appSecret: "shhh",
    });

    expect(mockPost).toHaveBeenCalledWith("/channels", expect.objectContaining({ appSecret: "shhh" }));
    expect(result.channelId).toBe("ch-new");
    const state = useChannelStore.getState();
    expect(state.channels).toHaveLength(2);
    expect(state.channels.map((c) => c.channelId)).toContain("ch-new");
  });

  it("updateChannel 走 PUT 并合并更新到匹配频道", async () => {
    mockPut.mockResolvedValue(undefined);

    useChannelStore.setState({
      channels: [
        makeChannel({ channelId: "ch-1", channelName: "Old" }),
        makeChannel({ channelId: "ch-2", channelName: "Other" }),
      ],
      loading: false,
      error: null,
    });

    await useChannelStore.getState().updateChannel("ch-1", { channelName: "New Name" });

    expect(mockPut).toHaveBeenCalledWith("/channels/ch-1", { channelName: "New Name" });
    const state = useChannelStore.getState();
    expect(state.channels.find((c) => c.channelId === "ch-1")?.channelName).toBe("New Name");
    expect(state.channels.find((c) => c.channelId === "ch-2")?.channelName).toBe("Other");
  });

  it("updateChannel 对 channelId 做 URL 编码", async () => {
    mockPut.mockResolvedValue(undefined);
    useChannelStore.setState({
      channels: [makeChannel({ channelId: "ch with space" })],
      loading: false,
      error: null,
    });

    await useChannelStore.getState().updateChannel("ch with space", { status: "paused" });

    expect(mockPut).toHaveBeenCalledWith("/channels/ch%20with%20space", { status: "paused" });
  });

  it("deleteChannel 调用 DELETE 并从列表移除频道", async () => {
    mockDelete.mockResolvedValue(undefined);

    useChannelStore.setState({
      channels: [
        makeChannel({ channelId: "ch-1" }),
        makeChannel({ channelId: "ch-2" }),
      ],
      loading: false,
      error: null,
    });

    await useChannelStore.getState().deleteChannel("ch-1");

    expect(mockDelete).toHaveBeenCalledWith("/channels/ch-1");
    const state = useChannelStore.getState();
    expect(state.channels).toHaveLength(1);
    expect(state.channels[0].channelId).toBe("ch-2");
  });
});
