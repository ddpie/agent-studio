import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// --- Polyfills for Node.js test environment ---
const store: Record<string, string> = {};
const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value; },
  removeItem: (key: string) => { delete store[key]; },
  clear: () => { for (const k in store) delete store[k]; },
  get length() { return Object.keys(store).length; },
  key: (i: number) => Object.keys(store)[i] ?? null,
};
Object.defineProperty(globalThis, "localStorage", { value: localStorageMock, writable: true });

// --- Mocks ---
vi.mock("aws-amplify/auth", () => ({
  fetchAuthSession: vi.fn().mockResolvedValue({
    tokens: { idToken: { toString: () => "mock-token" } },
  }),
  getCurrentUser: vi.fn().mockResolvedValue({ username: "test-user" }),
}));

vi.mock("../../config", () => ({
  agentConfig: { apiUrl: "https://test.example.com" },
}));

vi.mock("../api-client", () => ({
  getWorkspaceId: vi.fn().mockReturnValue("ws-test"),
}));

vi.mock("../../stores/ui-settings-store", () => ({
  useUISettings: { getState: () => ({ language: "en" }) },
}));

import { invokeMetaAgent, invokeAgentById } from "../agentcore-client";
import type { ChatMessage } from "../agentcore-client";

// --- Helpers ---

/** Encode text to SSE data line */
function sseDataLine(content: string): string {
  return `data: ${content}\n`;
}

/** Create a mock ReadableStream from an array of string chunks */
function createMockStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index++;
      } else {
        controller.close();
      }
    },
  });
}

/** Create a mock Response with SSE content-type */
function createSSEResponse(chunks: string[], status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "content-type": "text/event-stream" }),
    body: createMockStream(chunks),
    text: () => Promise.resolve(chunks.join("")),
  } as unknown as Response;
}

/** Create a mock error Response (non-streaming) */
function createErrorResponse(status: number, body = ""): Response {
  return {
    ok: false,
    status,
    headers: new Headers({ "content-type": "application/json" }),
    body: null,
    text: () => Promise.resolve(body),
  } as unknown as Response;
}

/** Collect all yielded chunks from an async generator */
async function collectChunks(gen: AsyncGenerator<string>): Promise<string[]> {
  const chunks: string[] = [];
  for await (const chunk of gen) {
    chunks.push(chunk);
  }
  return chunks;
}

// --- Setup ---
beforeEach(() => {
  vi.restoreAllMocks();
  localStorageMock.clear();
  globalThis.fetch = vi.fn();
  // crypto.subtle.digest mock for content-sha256
  if (!globalThis.crypto?.subtle) {
    Object.defineProperty(globalThis, "crypto", {
      value: {
        subtle: {
          digest: vi.fn().mockResolvedValue(new ArrayBuffer(32)),
        },
        randomUUID: () => "00000000-0000-0000-0000-000000000000",
        getRandomValues: (arr: Uint8Array) => arr,
      },
      writable: true,
      configurable: true,
    });
  } else {
    vi.spyOn(crypto.subtle, "digest").mockResolvedValue(new ArrayBuffer(32));
  }
  // Default navigator.onLine
  Object.defineProperty(navigator, "onLine", { value: true, writable: true, configurable: true });
});

afterEach(() => {
  vi.useRealTimers();
});

// --- Tests ---

describe("agentcore-client", () => {
  describe("SSE stream parsing — normal text lines", () => {
    it("yields plain text content from data: lines", async () => {
      const response = createSSEResponse([
        sseDataLine("Hello") + sseDataLine("World"),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["Hello", "World"]);
    });

    it("yields content split across multiple stream chunks", async () => {
      const response = createSSEResponse([
        "data: First\n",
        "data: Second\n",
        "data: Third\n",
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["First", "Second", "Third"]);
    });

    it("unwraps JSON-quoted strings in data: lines", async () => {
      const response = createSSEResponse([
        sseDataLine('"Hello \\"quoted\\" text"'),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(['Hello "quoted" text']);
    });

    it("skips empty data lines", async () => {
      const response = createSSEResponse([
        sseDataLine("") + sseDataLine("content") + sseDataLine(""),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["content"]);
    });
  });

  describe("SSE stream parsing — JSON __tool markers", () => {
    it("yields __tool start marker", async () => {
      const toolStart = JSON.stringify({ __tool: "start", name: "search_web", call_id: "c1" });
      const response = createSSEResponse([sseDataLine(toolStart)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(1);
      const parsed = JSON.parse(chunks[0]);
      expect(parsed.__tool).toBe("start");
      expect(parsed.name).toBe("search_web");
    });

    it("yields __tool result marker with base64 encoded input/output", async () => {
      const input = btoa("search query");
      const output = btoa("search results here");
      const toolResult = JSON.stringify({
        __tool: "result",
        call_id: "c1",
        input,
        output,
      });
      const response = createSSEResponse([sseDataLine(toolResult)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(1);
      const parsed = JSON.parse(chunks[0]);
      expect(parsed.__tool).toBe("result");
      // Verify the base64 content is passed through (decoding is consumer's job)
      expect(atob(parsed.input)).toBe("search query");
      expect(atob(parsed.output)).toBe("search results here");
    });

    it("yields __tool end marker", async () => {
      const toolEnd = JSON.stringify({ __tool: "end", call_id: "c1" });
      const response = createSSEResponse([sseDataLine(toolEnd)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(1);
      const parsed = JSON.parse(chunks[0]);
      expect(parsed.__tool).toBe("end");
    });

    it("yields full tool lifecycle (start → result → end) in order", async () => {
      const toolStart = JSON.stringify({ __tool: "start", name: "read_file", call_id: "c2" });
      const toolResult = JSON.stringify({ __tool: "result", call_id: "c2", input: btoa("/tmp/f"), output: btoa("content") });
      const toolEnd = JSON.stringify({ __tool: "end", call_id: "c2" });
      const response = createSSEResponse([
        sseDataLine("Before tool") +
        sseDataLine(toolStart) +
        sseDataLine(toolResult) +
        sseDataLine(toolEnd) +
        sseDataLine("After tool"),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(5);
      expect(chunks[0]).toBe("Before tool");
      expect(JSON.parse(chunks[1]).__tool).toBe("start");
      expect(JSON.parse(chunks[2]).__tool).toBe("result");
      expect(JSON.parse(chunks[3]).__tool).toBe("end");
      expect(chunks[4]).toBe("After tool");
    });
  });

  describe("Keepalive handling", () => {
    it("filters out __keepalive frames — they produce no user-visible content", async () => {
      const keepalive = JSON.stringify({ __keepalive: true, ts: 1234567890 });
      const response = createSSEResponse([
        sseDataLine("Hello") +
        sseDataLine(keepalive) +
        sseDataLine("World"),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["Hello", "World"]);
    });

    it("filters keepalive even when embedded in quoted string", async () => {
      // The keepalive check uses .includes('"__keepalive"')
      const keepalive = `"{\\"__keepalive\\":true}"`;
      const response = createSSEResponse([
        sseDataLine(keepalive),
        sseDataLine("visible"),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      // After JSON.parse of the quoted string, it becomes {"__keepalive":true}
      // which includes "__keepalive" and should be filtered
      expect(chunks).toEqual(["visible"]);
    });
  });

  describe("424 cold-start retry", () => {
    it("retries up to 3 times on 424, then succeeds", async () => {
      vi.useFakeTimers();
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch
        .mockResolvedValueOnce(createErrorResponse(424, "initializing"))
        .mockResolvedValueOnce(createErrorResponse(424, "initializing"))
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("success")])
        );

      const onStatus = vi.fn();
      const genPromise = collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1", onStatus)
      );

      // Advance through retry delays (5s each)
      await vi.advanceTimersByTimeAsync(5000);
      await vi.advanceTimersByTimeAsync(5000);

      const chunks = await genPromise;

      expect(mockFetch).toHaveBeenCalledTimes(3);
      expect(chunks).toEqual(["success"]);
      expect(onStatus).toHaveBeenCalledWith(
        expect.stringContaining("initializing")
      );
    });

    it("throws after exhausting all 424 retries", async () => {
      vi.useFakeTimers();
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      // 4 attempts (initial + 3 retries), all return 424
      mockFetch.mockResolvedValue(createErrorResponse(424, "cold"));

      // Attach the .catch immediately to prevent unhandled rejection
      let caughtError: Error | null = null;
      const genPromise = collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      ).catch((e) => { caughtError = e; return [] as string[]; });

      // Advance through 3 retry delays (attempts 0, 1, 2 each wait 5s)
      for (let i = 0; i < 3; i++) {
        await vi.advanceTimersByTimeAsync(5000);
      }
      await vi.advanceTimersByTimeAsync(0);
      await genPromise;

      expect(caughtError).not.toBeNull();
      expect(caughtError!.message).toContain("AgentCore error 424");
      expect(mockFetch).toHaveBeenCalledTimes(4);
    });

    it("calls onStatus with initialization message during 424 retry", async () => {
      vi.useFakeTimers();
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch
        .mockResolvedValueOnce(createErrorResponse(424, "busy"))
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("ok")])
        );

      const onStatus = vi.fn();
      const genPromise = collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1", onStatus)
      );

      await vi.advanceTimersByTimeAsync(5000);
      await genPromise;

      expect(onStatus).toHaveBeenCalledWith(
        expect.stringMatching(/Agent is initializing.*attempt 1\/3/)
      );
      // Status cleared on success
      expect(onStatus).toHaveBeenCalledWith(null);
    });
  });

  describe("403 retry", () => {
    it("retries once on first 403 (token refresh), then succeeds", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch
        .mockResolvedValueOnce(createErrorResponse(403, "expired"))
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("after refresh")])
        );

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(chunks).toEqual(["after refresh"]);
    });

    it("does not retry 403 on second attempt — throws", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      // First call: 403 → retry. Second call: 403 again → should stop.
      mockFetch
        .mockResolvedValueOnce(createErrorResponse(403, "forbidden"))
        .mockResolvedValueOnce(createErrorResponse(403, "forbidden"));

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("AgentCore error 403");
    });
  });

  describe("Abort/cancel handling", () => {
    it("stops reading when the stream reader is cancelled", async () => {
      // Create a stream that would hang if not cancelled
      let readerCancelled = false;
      const slowStream = new ReadableStream<Uint8Array>({
        start(controller) {
          const encoder = new TextEncoder();
          controller.enqueue(encoder.encode("data: first\n"));
          // Second chunk never comes — simulates abort mid-stream
        },
        cancel() {
          readerCancelled = true;
        },
      });

      const response = {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "text/event-stream" }),
        body: slowStream,
        text: () => Promise.resolve(""),
      } as unknown as Response;

      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const gen = invokeAgentById("agent-1", "hi", [], "sess-1");
      const firstResult = await gen.next();
      expect(firstResult.value).toBe("first");

      // Force-close the generator (simulates user abort)
      await gen.return(undefined as unknown as string);
      // The finally block in parseSSEStream should cancel the reader
      expect(readerCancelled).toBe(true);
    });
  });

  describe("Error handling", () => {
    it("throws on network error (fetch rejection)", async () => {
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error("Network failure")
      );

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("Network failure");
    });

    it("throws on non-retryable HTTP 400 immediately", async () => {
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        createErrorResponse(400, "Bad request body")
      );

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("AgentCore error 400");
    });

    it("throws on HTTP 500 immediately", async () => {
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        createErrorResponse(500, "Internal server error")
      );

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("AgentCore error 500");
    });

    it("throws on non-SSE 200 response (Lambda crash with JSON body)", async () => {
      const crashResponse = {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "application/json" }),
        body: null,
        text: () => Promise.resolve(JSON.stringify({ errorMessage: "Module load failed" })),
      } as unknown as Response;

      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(crashResponse);

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("Module load failed");
    });

    it("throws on non-SSE 200 with raw text body", async () => {
      const crashResponse = {
        ok: true,
        status: 200,
        headers: new Headers({ "content-type": "text/plain" }),
        body: null,
        text: () => Promise.resolve("runtime exited unexpectedly"),
      } as unknown as Response;

      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(crashResponse);

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("runtime exited unexpectedly");
    });
  });

  describe("Partial JSON line handling — incomplete lines across chunks", () => {
    it("reassembles a data: line split across two chunks", async () => {
      const response = createSSEResponse([
        "data: Hel",
        "lo World\n",
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["Hello World"]);
    });

    it("handles JSON __tool marker split across chunks", async () => {
      const toolJson = JSON.stringify({ __tool: "start", name: "calc", call_id: "x" });
      const fullLine = `data: ${toolJson}\n`;
      const splitAt = Math.floor(fullLine.length / 2);

      const response = createSSEResponse([
        fullLine.slice(0, splitAt),
        fullLine.slice(splitAt),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(1);
      expect(JSON.parse(chunks[0]).__tool).toBe("start");
    });

    it("handles multiple lines in a single chunk with trailing partial", async () => {
      const response = createSSEResponse([
        "data: line1\ndata: line2\ndata: par",
        "tial3\n",
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual(["line1", "line2", "partial3"]);
    });
  });

  describe("__error frame handling", () => {
    it("throws Error with the __error message content", async () => {
      const errorFrame = JSON.stringify({ __error: "rate_limit_exceeded" });
      const response = createSSEResponse([sseDataLine(errorFrame)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("rate_limit_exceeded");
    });

    it("throws on tool_schema_missing error frame", async () => {
      const errorFrame = JSON.stringify({ __error: "tool_schema_missing" });
      const response = createSSEResponse([sseDataLine(errorFrame)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      await expect(
        collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"))
      ).rejects.toThrow("tool_schema_missing");
    });

    it("yields content before __error frame, then throws", async () => {
      const errorFrame = JSON.stringify({ __error: "internal_failure" });
      const response = createSSEResponse([
        sseDataLine("partial output") + sseDataLine(errorFrame),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks: string[] = [];
      await expect(async () => {
        for await (const chunk of invokeAgentById("agent-1", "hi", [], "sess-1")) {
          chunks.push(chunk);
        }
      }).rejects.toThrow("internal_failure");

      expect(chunks).toEqual(["partial output"]);
    });

    it("treats malformed __error JSON as normal content (no throw)", async () => {
      // Starts with {"__error" but is not valid JSON
      const malformed = '{"__error": broken json}';
      const response = createSSEResponse([sseDataLine(malformed)]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      // Should not throw — falls through to normal yield
      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toEqual([malformed]);
    });
  });

  describe("__auto_continue frame handling", () => {
    it("yields __auto_continue frames as-is for chat-store to parse", async () => {
      const autoContinue = JSON.stringify({ __auto_continue: { attempt: 1, max: 3 } });
      const response = createSSEResponse([
        sseDataLine("text before") +
        sseDataLine(autoContinue) +
        sseDataLine("text after"),
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      expect(chunks).toHaveLength(3);
      expect(chunks[0]).toBe("text before");
      expect(JSON.parse(chunks[1]).__auto_continue).toEqual({ attempt: 1, max: 3 });
      expect(chunks[2]).toBe("text after");
    });
  });

  describe("Multiple concurrent invocations", () => {
    it("two concurrent invocations do not interfere with each other", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;

      // Each invocation gets its own response
      mockFetch
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("agent-A-chunk1") + sseDataLine("agent-A-chunk2")])
        )
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("agent-B-chunk1") + sseDataLine("agent-B-chunk2")])
        );

      const [chunksA, chunksB] = await Promise.all([
        collectChunks(invokeAgentById("agentA", "hello A", [], "sess-A")),
        collectChunks(invokeAgentById("agentB", "hello B", [], "sess-B")),
      ]);

      expect(chunksA).toEqual(["agent-A-chunk1", "agent-A-chunk2"]);
      expect(chunksB).toEqual(["agent-B-chunk1", "agent-B-chunk2"]);
    });

    it("error in one invocation does not affect the other", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;

      mockFetch
        .mockResolvedValueOnce(createErrorResponse(500, "crash"))
        .mockResolvedValueOnce(
          createSSEResponse([sseDataLine("success")])
        );

      const [resultA, resultB] = await Promise.allSettled([
        collectChunks(invokeAgentById("agentA", "hello A", [], "sess-A")),
        collectChunks(invokeAgentById("agentB", "hello B", [], "sess-B")),
      ]);

      expect(resultA.status).toBe("rejected");
      expect(resultB.status).toBe("fulfilled");
      if (resultB.status === "fulfilled") {
        expect(resultB.value).toEqual(["success"]);
      }
    });
  });

  describe("invokeMetaAgent", () => {
    it("passes prompt, history, and session to the invoke endpoint", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch.mockResolvedValue(
        createSSEResponse([sseDataLine("meta response")])
      );

      const history: ChatMessage[] = [
        { role: "user", content: "create an agent" },
        { role: "assistant", content: "Sure!" },
      ];

      const chunks = await collectChunks(
        invokeMetaAgent("do something", history, "sess-meta")
      );

      expect(chunks).toEqual(["meta response"]);
      expect(mockFetch).toHaveBeenCalledWith(
        "https://test.example.com/invoke/workspaces/ws-test/meta-agent",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"prompt":"do something"'),
        })
      );
      // Verify body contains session_id and caller_id
      const callBody = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
      expect(callBody.session_id).toBe("sess-meta");
      expect(callBody.caller_id).toBe("test-user");
      expect(callBody.language).toBe("en");
    });

    it("retries on tool_schema_missing error with fresh session ID", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      const errorFrame = JSON.stringify({ __error: "tool_schema_missing" });

      // First attempt: tool_schema_missing error
      // Second attempt: success
      mockFetch
        .mockResolvedValueOnce(createSSEResponse([sseDataLine(errorFrame)]))
        .mockResolvedValueOnce(createSSEResponse([sseDataLine("works now")]));

      const onStatus = vi.fn();
      const chunks = await collectChunks(
        invokeMetaAgent("test", [], "orig-sess", onStatus)
      );

      expect(chunks).toEqual(["works now"]);
      expect(mockFetch).toHaveBeenCalledTimes(2);
      // Second call should have a different session_id (fresh)
      const firstBody = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
      const secondBody = JSON.parse((mockFetch.mock.calls[1][1] as RequestInit).body as string);
      expect(secondBody.session_id).not.toBe(firstBody.session_id);
      // onStatus should report the retry
      expect(onStatus).toHaveBeenCalledWith(
        expect.stringContaining("Tool registration glitch")
      );
    });

    it("passes mode parameter when specified", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch.mockResolvedValue(
        createSSEResponse([sseDataLine("skill edit response")])
      );

      await collectChunks(
        invokeMetaAgent("edit this", [], "sess-1", undefined, undefined, undefined, "skill_edit")
      );

      const callBody = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
      expect(callBody.mode).toBe("skill_edit");
    });
  });

  describe("Request headers", () => {
    it("includes X-Auth-Token, content-type, x-amz-content-sha256, and Accept headers", async () => {
      const mockFetch = globalThis.fetch as ReturnType<typeof vi.fn>;
      mockFetch.mockResolvedValue(
        createSSEResponse([sseDataLine("ok")])
      );

      await collectChunks(invokeAgentById("agent-1", "hi", [], "sess-1"));

      const [, init] = mockFetch.mock.calls[0];
      expect(init.headers["X-Auth-Token"]).toBe("mock-token");
      expect(init.headers["Content-Type"]).toBe("application/json");
      expect(init.headers.Accept).toBe("text/event-stream");
      expect(init.headers["x-amz-content-sha256"]).toBeDefined();
    });
  });

  describe("Non-data lines in SSE", () => {
    it("ignores comment lines (starting with :) and other non-data lines", async () => {
      const response = createSSEResponse([
        ": this is a comment\n" +
        "event: message\n" +
        "data: actual content\n" +
        "id: 123\n",
      ]);
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(response);

      const chunks = await collectChunks(
        invokeAgentById("agent-1", "hi", [], "sess-1")
      );

      // Only "data:" lines yield content
      expect(chunks).toEqual(["actual content"]);
    });
  });
});
