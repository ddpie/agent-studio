import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { setupServer } from "msw/node";
import {
  MemoryRecordSchema,
  MemorySectionSchema,
  MemoryBundleSchema,
} from "../../lib/contracts";
import { handlers, BASE, WS_ID, makeMemoryRecord } from "../../mocks/handlers";

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("Memory contract schemas", () => {
  it("validates the full memory bundle from MSW", async () => {
    const resp = await fetch(
      `${BASE}/api/workspaces/${WS_ID}/agents/agent-001/my-memories`,
    );
    const data = await resp.json();

    const parsed = MemoryBundleSchema.parse(data);
    expect(parsed.preferences.records).toHaveLength(1);
    expect(parsed.facts.nextToken).toBe("token-facts-page2");
    expect(parsed.summaries.records).toHaveLength(0);
    expect(parsed.episodes.records).toHaveLength(0);
  });

  it("validates a memory record with text content", () => {
    const record = makeMemoryRecord();
    const parsed = MemoryRecordSchema.parse(record);
    expect(parsed.id).toBe("mem-rec-001");
    expect((parsed.content as { text?: string }).text).toBe("User prefers dark mode");
  });

  it("validates a memory record with arbitrary object content", () => {
    const record = makeMemoryRecord({
      content: { summary: "Meeting notes", participants: 5 },
    });
    const parsed = MemoryRecordSchema.parse(record);
    expect(parsed.content).toHaveProperty("summary");
  });

  it("rejects a memory record missing 'createdAt'", () => {
    const invalid = { id: "bad", content: { text: "hi" }, namespace: "facts" };
    const result = MemoryRecordSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes("createdAt"))).toBe(true);
    }
  });

  it("rejects a memory section with non-array records", () => {
    const invalid = { records: "not-an-array", nextToken: null };
    const result = MemorySectionSchema.safeParse(invalid);
    expect(result.success).toBe(false);
  });

  it("rejects a memory bundle missing a required section", () => {
    const invalid = {
      preferences: { records: [], nextToken: null },
      facts: { records: [], nextToken: null },
      summaries: { records: [], nextToken: null },
      // missing: episodes
    };
    const result = MemoryBundleSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes("episodes"))).toBe(true);
    }
  });
});
