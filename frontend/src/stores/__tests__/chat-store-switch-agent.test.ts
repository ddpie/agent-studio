/**
 * Regression guard for per-agent message isolation.
 *
 * Before the per-agent refactor, `sendMessage` wrote chunks to a
 * top-level `state.messages` array. If the user navigated away to
 * another agent mid-stream, `switchAgent` replaced `state.messages`
 * and subsequent chunks for the old agent silently vanished — when
 * the user navigated back, the reply was truncated to whatever had
 * arrived before the switch.
 *
 * These tests exercise the full sendMessage path with a mocked SSE
 * stream, switch agents mid-stream, and verify the previous agent's
 * bucket still receives the remaining chunks.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock i18next so deriveTitle + error path don't crash.
vi.mock("i18next", () => ({
  default: {
    t: (_key: string, fallback?: string) => fallback || _key,
  },
}));

// Controllable mock streams, one per agent. Tests push chunks into a queue
// and then resolve/close the stream to simulate streaming arrival order.
type ChunkSource = {
  enqueue: (chunk: string) => void;
  close: () => void;
};

const streamSources = new Map<string, ChunkSource>();

function makeAsyncGenerator(key: string): AsyncGenerator<string> {
  const chunks: string[] = [];
  let resolvers: (() => void)[] = [];
  let closed = false;

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
  };
  streamSources.set(key, source);

  async function* gen(): AsyncGenerator<string> {
    while (true) {
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

import { useChatStore } from "../chat-store";

beforeEach(() => {
  streamSources.clear();
  useChatStore.setState({
    currentAgentId: null,
    currentAgentName: null,
    messagesByAgent: {},
    streamingByAgent: {},
    statusByAgent: {},
    activeToolByAgent: {},
    sessionIdByAgent: {},
    activeSessionByAgent: {},
    selectedModelByAgent: {},
    sessions: [],
    lastActiveSessionByAgent: {},
  });
});

async function flush() {
  // Let pending promises + the 50ms batch flush timer fire.
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 20));
}

describe("per-agent message isolation", () => {
  it("chunks for agent A keep landing in A's bucket after user switches to agent B", async () => {
    const store = useChatStore.getState();

    // Start chat with agent A
    store.switchAgent("agent-a", "Agent A");
    const sendPromise = store.sendMessage("hello A");

    await flush();
    const srcA = streamSources.get("agent-a")!;
    expect(srcA).toBeDefined();

    // A receives first chunk
    srcA.enqueue("part 1 ");
    await flush();
    expect(useChatStore.getState().messagesByAgent["agent-a"]?.at(-1)?.content).toBe("part 1 ");

    // Mid-stream, user navigates to agent B
    useChatStore.getState().switchAgent("agent-b", "Agent B");
    expect(useChatStore.getState().currentAgentId).toBe("agent-b");
    // A's messages still present
    expect(useChatStore.getState().messagesByAgent["agent-a"]?.length).toBe(2);

    // A's stream keeps running and its chunks keep landing in A's bucket
    srcA.enqueue("part 2");
    srcA.close();
    await sendPromise;
    await flush();

    const finalA = useChatStore.getState().messagesByAgent["agent-a"]?.at(-1)?.content;
    expect(finalA).toBe("part 1 part 2");

    // B's bucket is still empty — no cross-agent leakage
    expect(useChatStore.getState().messagesByAgent["agent-b"] || []).toEqual([]);
  });

  it("cancelStreaming only cancels the current agent's stream", async () => {
    const store = useChatStore.getState();

    store.switchAgent("agent-a", "Agent A");
    const pA = store.sendMessage("A");
    await flush();
    const srcA = streamSources.get("agent-a")!;

    useChatStore.getState().switchAgent("agent-b", "Agent B");
    const pB = useChatStore.getState().sendMessage("B");
    await flush();
    const srcB = streamSources.get("agent-b")!;

    // Cancelling on B should only abort B's stream
    useChatStore.getState().cancelStreaming();

    // A should still be streaming
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(true);
    expect(useChatStore.getState().streamingByAgent["agent-b"]).toBe(false);

    // Clean up: complete both streams
    srcA.enqueue("A done");
    srcA.close();
    srcB.close();
    await pA;
    await pB;
    await flush();
  });

  it("streamingByAgent flag reflects only the active agent", async () => {
    const store = useChatStore.getState();

    store.switchAgent("agent-a", "Agent A");
    const pA = store.sendMessage("A");
    await flush();
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(true);

    // Switch to B where nothing is streaming
    useChatStore.getState().switchAgent("agent-b", "Agent B");
    expect(!!useChatStore.getState().streamingByAgent["agent-b"]).toBe(false);
    // But A is still streaming in the background
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(true);

    // Complete A
    streamSources.get("agent-a")!.close();
    await pA;
    await flush();
    expect(useChatStore.getState().streamingByAgent["agent-a"]).toBe(false);
  });
});
