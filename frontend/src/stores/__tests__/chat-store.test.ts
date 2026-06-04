/**
 * Broader coverage for chat-store: covers session save/load/delete,
 * regenerate/edit-and-resend, sendMessage tool-call + S3 download
 * extraction, error path, and the cloud-hydration helper.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("i18next", () => ({
  default: {
    t: (_key: string, fallback?: string) => fallback || _key,
  },
}));

// Mock api-client cloud helpers — keep them silent and trackable.
const listChatSessionsMock = vi.fn(async () => null);
const getChatSessionMock = vi.fn(async () => null);
const putChatSessionMock = vi.fn(async () => undefined);
const deleteChatSessionMock = vi.fn(async () => undefined);

vi.mock("../../lib/api-client", () => ({
  listChatSessions: (...args: unknown[]) => listChatSessionsMock(...args),
  getChatSession: (...args: unknown[]) => getChatSessionMock(...args),
  putChatSession: (...args: unknown[]) => putChatSessionMock(...args),
  deleteChatSession: (...args: unknown[]) => deleteChatSessionMock(...args),
}));

// Controllable mock streams keyed by agentId (or "meta").
type ChunkSource = {
  enqueue: (chunk: string) => void;
  close: () => void;
  fail: (err: Error) => void;
};

const streamSources: Map<string, ChunkSource> = new Map();

function makeAsyncGenerator(key: string): AsyncGenerator<string> {
  const chunks: string[] = [];
  let resolvers: Array<() => void> = [];
  let closed = false;
  let failure: Error | null = null;

  const source: ChunkSource = {
    enqueue: (chunk: string) => {
      chunks.push(chunk);
      const r = resolvers.shift();
      if (r) r();
    },
    close: () => {
      closed = true;
      for (const r of resolvers) r();
      resolvers = [];
    },
    fail: (err: Error) => {
      failure = err;
      for (const r of resolvers) r();
      resolvers = [];
    },
  };
  streamSources.set(key, source);

  async function* gen(): AsyncGenerator<string> {
    while (true) {
      if (failure) throw failure;
      if (chunks.length > 0) {
        yield chunks.shift()!;
        continue;
      }
      if (closed) return;
      await new Promise<void>((res) => { resolvers.push(res); });
    }
  }
  return gen();
}

vi.mock("../../lib/agentcore-client", () => ({
  invokeAgentById: vi.fn((agentId: string) => makeAsyncGenerator(agentId)),
  invokeMetaAgent: vi.fn(() => makeAsyncGenerator("meta")),
}));

import { useChatStore, resetChatForWorkspaceSwitch, resetCloudHydrationCache, hydrateSessionsFromCloud, agentKey, type Message } from "../chat-store";

const initialState = {
  currentAgentId: null,
  currentAgentName: null,
  messagesByAgent: {} as Record<string, Message[]>,
  streamingByAgent: {},
  statusByAgent: {},
  activeToolByAgent: {},
  autoContinueByAgent: {},
  sessionIdByAgent: {},
  activeSessionByAgent: {},
  selectedModelByAgent: {},
  sessions: [],
  lastActiveSessionByAgent: {},
};

beforeEach(() => {
  streamSources.clear();
  vi.clearAllMocks();
  useChatStore.setState(initialState);
  resetCloudHydrationCache();
  try {
    window.localStorage.clear();
  } catch { /* ignore */ }
});

async function flush(times = 6) {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 20));
}

describe("agentKey helper", () => {
  it("maps null to 'meta'", () => {
    expect(agentKey(null)).toBe("meta");
  });
  it("returns the agentId for non-null input", () => {
    expect(agentKey("agt-1")).toBe("agt-1");
  });
});

describe("setSelectedModel", () => {
  it("stores the model under the current agent's bucket", () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    useChatStore.getState().setSelectedModel("claude-opus-4.6");
    expect(useChatStore.getState().selectedModelByAgent["agent-a"]).toBe("claude-opus-4.6");
  });
});

describe("newSession", () => {
  it("clears the current agent's bucket and resets sessionId/activeSession", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("hello");
    await flush();
    streamSources.get("agent-a")!.enqueue("partial answer");
    streamSources.get("agent-a")!.close();
    await p;
    await flush();
    // After completion, the session is saved and a fresh one should be active.
    expect(useChatStore.getState().sessions.length).toBeGreaterThan(0);
    useChatStore.getState().newSession();
    const s = useChatStore.getState();
    expect(s.messagesByAgent["agent-a"] || []).toHaveLength(0);
    expect(s.sessionIdByAgent["agent-a"]).toBeUndefined();
    expect(s.activeSessionByAgent["agent-a"]).toBe(null);
  });
});

describe("loadSession", () => {
  it("restores the messages of a previously saved session", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("first question");
    await flush();
    streamSources.get("agent-a")!.enqueue("first answer");
    streamSources.get("agent-a")!.close();
    await p;
    await flush();
    const sessionId = useChatStore.getState().sessions[0].id;
    // Start a new conversation, then reload the previous one.
    useChatStore.getState().newSession();
    expect(useChatStore.getState().messagesByAgent["agent-a"] || []).toHaveLength(0);
    useChatStore.getState().loadSession(sessionId);
    const restored = useChatStore.getState().messagesByAgent["agent-a"];
    expect(restored.length).toBe(2);
    expect(restored[0].role).toBe("user");
    expect(restored[0].content).toBe("first question");
    expect(useChatStore.getState().activeSessionByAgent["agent-a"]).toBe(sessionId);
  });

  it("is a no-op when sessionId does not exist", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      messagesByAgent: { "agent-a": [{ id: "m1", role: "user", content: "hi", timestamp: 0 }] },
    });
    useChatStore.getState().loadSession("nonexistent");
    // No throw, messages preserved.
    expect(useChatStore.getState().messagesByAgent["agent-a"]).toHaveLength(1);
  });
});

describe("deleteSession", () => {
  it("removes the session from sessions and calls deleteChatSession", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("temp");
    await flush();
    streamSources.get("agent-a")!.enqueue("temp answer");
    streamSources.get("agent-a")!.close();
    await p;
    await flush();
    const sessionId = useChatStore.getState().sessions[0].id;
    useChatStore.getState().deleteSession(sessionId);
    expect(useChatStore.getState().sessions).toHaveLength(0);
    expect(deleteChatSessionMock).toHaveBeenCalledWith("agent-a", sessionId);
    // Active session cleared as well.
    expect(useChatStore.getState().activeSessionByAgent["agent-a"]).toBe(null);
  });

  it("does not clear the bucket when deleting a non-active session", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      messagesByAgent: { "agent-a": [{ id: "m1", role: "user", content: "hi", timestamp: 0 }] },
      activeSessionByAgent: { "agent-a": "active-id" },
      sessions: [
        { id: "active-id", agentKey: "agent-a", title: "active", messages: [], createdAt: 1, updatedAt: 2 },
        { id: "other-id", agentKey: "agent-a", title: "other", messages: [], createdAt: 1, updatedAt: 1 },
      ],
    });
    useChatStore.getState().deleteSession("other-id");
    // Active untouched.
    expect(useChatStore.getState().messagesByAgent["agent-a"]).toHaveLength(1);
    expect(useChatStore.getState().activeSessionByAgent["agent-a"]).toBe("active-id");
    // Sessions array shrunk.
    expect(useChatStore.getState().sessions).toHaveLength(1);
  });
});

describe("clearMessages", () => {
  it("clears messages for the current agent only", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      messagesByAgent: {
        "agent-a": [{ id: "m1", role: "user", content: "hi", timestamp: 0 }],
        "agent-b": [{ id: "m2", role: "user", content: "yo", timestamp: 0 }],
      },
    });
    useChatStore.getState().clearMessages();
    expect(useChatStore.getState().messagesByAgent["agent-a"] || []).toHaveLength(0);
    expect(useChatStore.getState().messagesByAgent["agent-b"]).toHaveLength(1);
  });
});

describe("getAgentSessions", () => {
  it("returns only sessions for the current agent, sorted by updatedAt desc", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      sessions: [
        { id: "s1", agentKey: "agent-a", title: "old", messages: [], createdAt: 1, updatedAt: 1 },
        { id: "s2", agentKey: "agent-b", title: "other", messages: [], createdAt: 1, updatedAt: 100 },
        { id: "s3", agentKey: "agent-a", title: "new", messages: [], createdAt: 1, updatedAt: 50 },
      ],
    });
    const sessions = useChatStore.getState().getAgentSessions();
    expect(sessions.map((s) => s.id)).toEqual(["s3", "s1"]);
  });
});

describe("regenerateLastMessage", () => {
  it("trims back to last user message and re-issues with same content", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p1 = store.sendMessage("ask 1");
    await flush();
    streamSources.get("agent-a")!.enqueue("first answer");
    streamSources.get("agent-a")!.close();
    await p1;
    await flush();

    // Now regenerate.
    streamSources.clear();
    const p2 = useChatStore.getState().regenerateLastMessage();
    await flush();
    const src2 = streamSources.get("agent-a")!;
    expect(src2).toBeDefined();
    src2.enqueue("regenerated");
    src2.close();
    await p2;
    await flush();
    const msgs = useChatStore.getState().messagesByAgent["agent-a"];
    // Only one user msg + one new assistant msg.
    expect(msgs.length).toBe(2);
    expect(msgs[0].content).toBe("ask 1");
    expect(msgs[1].content).toBe("regenerated");
  });

  it("is a no-op when streaming", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      streamingByAgent: { "agent-a": true },
      messagesByAgent: { "agent-a": [{ id: "m1", role: "user", content: "x", timestamp: 0 }] },
    });
    // Should resolve without invoking sendMessage / making a stream.
    return useChatStore.getState().regenerateLastMessage();
  });

  it("is a no-op when no user message exists", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      messagesByAgent: { "agent-a": [] },
    });
    return useChatStore.getState().regenerateLastMessage();
  });
});

describe("editAndResend", () => {
  it("trims after the edited user message and re-sends with new content", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p1 = store.sendMessage("first");
    await flush();
    streamSources.get("agent-a")!.enqueue("first answer");
    streamSources.get("agent-a")!.close();
    await p1;
    await flush();

    const userMsgId = useChatStore.getState().messagesByAgent["agent-a"][0].id;

    streamSources.clear();
    const p2 = useChatStore.getState().editAndResend(userMsgId, "edited question");
    await flush();
    const src2 = streamSources.get("agent-a")!;
    src2.enqueue("edited answer");
    src2.close();
    await p2;
    await flush();

    const msgs = useChatStore.getState().messagesByAgent["agent-a"];
    expect(msgs[0].content).toBe("edited question");
    expect(msgs[1].content).toBe("edited answer");
  });

  it("is a no-op when message id is unknown", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      messagesByAgent: { "agent-a": [{ id: "m1", role: "user", content: "x", timestamp: 0 }] },
    });
    return useChatStore.getState().editAndResend("missing", "new");
  });

  it("is a no-op when streaming", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: "agent-a",
      streamingByAgent: { "agent-a": true },
      messagesByAgent: { "agent-a": [{ id: "m1", role: "user", content: "x", timestamp: 0 }] },
    });
    return useChatStore.getState().editAndResend("m1", "new");
  });
});

describe("sendMessage tool/download/error handling", () => {
  it("captures tool start/result/end markers as toolCalls and clears activeTool", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("compute");
    await flush();
    const src = streamSources.get("agent-a")!;
    src.enqueue('{"__tool":"start","name":"calc"}');
    await flush();
    expect(useChatStore.getState().activeToolByAgent["agent-a"]).toBe("calc");
    // base64 of "1+1" / "2"
    const inputB64 = btoa("1+1");
    const outputB64 = btoa("2");
    src.enqueue(`{"__tool":"result","name":"calc","input":"${inputB64}","output":"${outputB64}"}`);
    src.enqueue('{"__tool":"end","name":"calc"}');
    src.close();
    await p;
    await flush();
    const msg = useChatStore.getState().messagesByAgent["agent-a"][1];
    expect(msg.toolCalls).toHaveLength(1);
    expect(msg.toolCalls![0].name).toBe("calc");
    expect(msg.toolCalls![0].input).toBe("1+1");
    expect(msg.toolCalls![0].output).toBe("2");
    expect(useChatStore.getState().activeToolByAgent["agent-a"] ?? null).toBe(null);
  });

  it("extracts __S3_DOWNLOAD__ markers from prose into s3Downloads", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("upload");
    await flush();
    const src = streamSources.get("agent-a")!;
    src.enqueue("Here is your file. __S3_DOWNLOAD__:outputs/a.png:photo.png and done.");
    src.close();
    await p;
    await flush();
    const msg = useChatStore.getState().messagesByAgent["agent-a"][1];
    expect(msg.s3Downloads).toEqual([{ key: "outputs/a.png", filename: "photo.png" }]);
    expect(msg.content).not.toContain("__S3_DOWNLOAD__");
  });

  it("recognises auto-continue control frames and clears them on next chunk", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("compute");
    await flush();
    const src = streamSources.get("agent-a")!;
    src.enqueue('{"__auto_continue":2,"max":5}');
    await flush();
    expect(useChatStore.getState().autoContinueByAgent["agent-a"]).toEqual({ n: 2, max: 5 });
    src.enqueue("real text");
    await flush();
    expect(useChatStore.getState().autoContinueByAgent["agent-a"] ?? null).toBe(null);
    src.close();
    await p;
    await flush();
  });

  it("appends an error block when the stream rejects", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("hi");
    await flush();
    streamSources.get("agent-a")!.fail(new Error("network down"));
    await p;
    await flush();
    const msg = useChatStore.getState().messagesByAgent["agent-a"][1];
    const errBlock = (msg.blocks || []).find((b) => b.kind === "error");
    expect(errBlock).toBeDefined();
    expect(errBlock!.kind === "error" && errBlock.text).toContain("network down");
  });

  it("reuses sessionId across turns within the same chat", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");

    const p1 = store.sendMessage("first");
    await flush();
    streamSources.get("agent-a")!.enqueue("a");
    streamSources.get("agent-a")!.close();
    await p1;
    await flush();
    const sid1 = useChatStore.getState().sessionIdByAgent["agent-a"];
    expect(sid1).toBeTruthy();

    streamSources.clear();
    const p2 = useChatStore.getState().sendMessage("second");
    await flush();
    streamSources.get("agent-a")!.enqueue("b");
    streamSources.get("agent-a")!.close();
    await p2;
    await flush();
    const sid2 = useChatStore.getState().sessionIdByAgent["agent-a"];
    expect(sid2).toBe(sid1);
  });
});

describe("resetChatForWorkspaceSwitch", () => {
  it("aborts in-flight streams and wipes state", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("hello");
    await flush();
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(true);

    resetChatForWorkspaceSwitch();
    // Drain the stream so the suspended generator can finish.
    streamSources.get("agent-a")?.close();
    await p;
    await flush();

    const s = useChatStore.getState();
    expect(s.currentAgentId).toBe(null);
    expect(s.sessions).toEqual([]);
    expect(s.messagesByAgent).toEqual({});
  });
});

describe("hydrateSessionsFromCloud", () => {
  it("merges fetched sessions into local state", async () => {
    listChatSessionsMock.mockResolvedValueOnce([{ id: "cloud-1" }] as never);
    getChatSessionMock.mockResolvedValueOnce({
      id: "cloud-1",
      agentKey: "agent-x",
      title: "From cloud",
      messages: [{ id: "m1", role: "user", content: "remote", timestamp: 0 }],
      createdAt: 1,
      updatedAt: 99,
    } as never);

    await hydrateSessionsFromCloud("agent-x");
    const sessions = useChatStore.getState().sessions;
    expect(sessions.find((s) => s.id === "cloud-1")?.title).toBe("From cloud");
  });

  it("is a no-op on the second call (already hydrated)", async () => {
    listChatSessionsMock.mockResolvedValueOnce([] as never);
    await hydrateSessionsFromCloud("agent-y");
    expect(listChatSessionsMock).toHaveBeenCalledTimes(1);

    await hydrateSessionsFromCloud("agent-y");
    // Still 1 — second call short-circuits.
    expect(listChatSessionsMock).toHaveBeenCalledTimes(1);
  });

  it("recovers (re-arms) when the listing fails", async () => {
    listChatSessionsMock.mockRejectedValueOnce(new Error("offline"));
    await hydrateSessionsFromCloud("agent-z");
    // Should have re-deleted itself from the gate so a retry can run.
    listChatSessionsMock.mockResolvedValueOnce([] as never);
    await hydrateSessionsFromCloud("agent-z");
    expect(listChatSessionsMock).toHaveBeenCalledTimes(2);
  });
});

describe("cancelStreaming", () => {
  it("flips streamingByAgent to false for the current agent", async () => {
    const store = useChatStore.getState();
    store.switchAgent("agent-a", "Agent A");
    const p = store.sendMessage("go");
    await flush();
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(true);
    useChatStore.getState().cancelStreaming();
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(false);
    // Allow generator to settle.
    streamSources.get("agent-a")!.close();
    await p;
    await flush();
  });
});

describe("switchAgent restores messages when bucket is empty", () => {
  it("rehydrates messagesByAgent from the most recent session for the target agent", () => {
    useChatStore.setState({
      ...initialState,
      currentAgentId: null,
      sessions: [
        {
          id: "sess-old",
          agentKey: "agent-x",
          title: "Old",
          messages: [{ id: "m-old", role: "user", content: "old", timestamp: 1 }],
          modelId: "m-1",
          createdAt: 1,
          updatedAt: 10,
        },
        {
          id: "sess-new",
          agentKey: "agent-x",
          title: "New",
          messages: [{ id: "m-new", role: "user", content: "new", timestamp: 2 }],
          modelId: "m-2",
          createdAt: 2,
          updatedAt: 20,
        },
      ],
    });
    useChatStore.getState().switchAgent("agent-x", "Agent X");
    const restored = useChatStore.getState().messagesByAgent["agent-x"];
    expect(restored.map((m) => m.id)).toEqual(["m-new"]);
    expect(useChatStore.getState().activeSessionByAgent["agent-x"]).toBe("sess-new");
    expect(useChatStore.getState().selectedModelByAgent["agent-x"]).toBe("m-2");
  });
});
