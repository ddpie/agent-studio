import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { setupServer } from "msw/node";
import {
  RunSummarySchema,
  RunDetailSchema,
  ListRunsResponseSchema,
} from "../../lib/contracts";
import { handlers, BASE, WS_ID, makeRunSummary } from "../../mocks/handlers";

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("Runs contract schemas", () => {
  it("validates the list runs response from MSW", async () => {
    const resp = await fetch(
      `${BASE}/api/workspaces/${WS_ID}/agents/agent-001/runs?limit=20`,
    );
    const data = await resp.json();

    const parsed = ListRunsResponseSchema.parse(data);
    expect(parsed.runs).toHaveLength(3);
    expect(parsed.runs[0].status).toBe("completed");
    expect(parsed.runs[1].completedAt).toBeNull();
    expect(parsed.runs[2].trigger).toBe("schedule");
  });

  it("validates a run summary with nullable fields as null", () => {
    const run = makeRunSummary({
      model: null,
      totalTokens: null,
      durationMs: null,
      completedAt: null,
    });
    const parsed = RunSummarySchema.parse(run);
    expect(parsed.model).toBeNull();
    expect(parsed.totalTokens).toBeNull();
  });

  it("rejects a run with invalid trigger value", () => {
    const invalid = makeRunSummary({ trigger: "cron" });
    const result = RunSummarySchema.safeParse(invalid);
    expect(result.success).toBe(false);
  });

  it("rejects a run with invalid status value", () => {
    const invalid = makeRunSummary({ status: "cancelled" });
    const result = RunSummarySchema.safeParse(invalid);
    expect(result.success).toBe(false);
  });

  it("validates a complete RunDetail structure", () => {
    const detail = {
      runId: "run-full",
      trigger: "manual" as const,
      scheduleId: null,
      sessionId: "sess-001",
      status: "completed" as const,
      input: "Test prompt",
      outputUrl: "https://s3.example.com/output.json",
      artifactRefs: [{ key: "artifacts/file.csv", filename: "file.csv" }],
      usage: { promptTokens: 500, completionTokens: 1000, totalTokens: 1500 },
      durationMs: 2500,
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0",
      error: null,
      startedAt: "2026-01-15T10:00:00Z",
      completedAt: "2026-01-15T10:00:02Z",
    };
    const parsed = RunDetailSchema.parse(detail);
    expect(parsed.artifactRefs[0].filename).toBe("file.csv");
    expect(parsed.usage.totalTokens).toBe(1500);
  });

  it("rejects RunDetail with missing usage sub-object", () => {
    const invalid = {
      runId: "run-bad",
      trigger: "manual",
      scheduleId: null,
      sessionId: null,
      status: "completed",
      input: "test",
      outputUrl: null,
      artifactRefs: [],
      // missing: usage
      durationMs: null,
      model: null,
      error: null,
      startedAt: "2026-01-15T10:00:00Z",
      completedAt: null,
    };
    const result = RunDetailSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes("usage"))).toBe(true);
    }
  });
});
