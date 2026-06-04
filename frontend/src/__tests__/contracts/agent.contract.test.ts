import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { setupServer } from "msw/node";
import {
  AgentListItemSchema,
  PaginatedAgentsSchema,
  AgentRuntimeInfoSchema,
  validateResponse,
} from "../../lib/contracts";
import { handlers, BASE, WS_ID, makeAgent } from "../../mocks/handlers";

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("Agent contract schemas", () => {
  it("validates a well-formed agent list response", async () => {
    const resp = await fetch(`${BASE}/api/workspaces/${WS_ID}/agents?limit=10`);
    const data = await resp.json();

    const parsed = PaginatedAgentsSchema.parse(data);
    expect(parsed.items).toHaveLength(3);
    expect(parsed.items[0].agentId).toBe("agent-001");
    expect(parsed.items[0].status).toBe("READY");
  });

  it("validates a single agent response", async () => {
    const resp = await fetch(`${BASE}/api/workspaces/${WS_ID}/agents/agent-042`);
    const data = await resp.json();

    const parsed = AgentListItemSchema.parse(data);
    expect(parsed.agentId).toBe("agent-042");
    expect(parsed.name).toBe("TestAgent");
  });

  it("rejects an agent missing required field 'agentId'", () => {
    const invalid = { name: "NoId", status: "READY" };
    const result = AgentListItemSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes("agentId"))).toBe(true);
    }
  });

  it("rejects an agent with wrong type for 'status'", () => {
    const invalid = makeAgent({ status: 123 });
    const result = AgentListItemSchema.safeParse(invalid);
    expect(result.success).toBe(false);
  });

  it("rejects invalid runtime_type enum value", () => {
    const invalid = makeAgent({ runtime_type: "docker" });
    const result = AgentListItemSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].path).toContain("runtime_type");
    }
  });

  it("validates AgentRuntimeInfo with valid status", () => {
    const data = { status: "READY", lastUpdatedAt: "2026-01-15T10:00:00Z" };
    const parsed = AgentRuntimeInfoSchema.parse(data);
    expect(parsed.status).toBe("READY");
  });

  it("rejects AgentRuntimeInfo with invalid status value", () => {
    const data = { status: "UNKNOWN_STATE" };
    const result = AgentRuntimeInfoSchema.safeParse(data);
    expect(result.success).toBe(false);
  });

  it("validateResponse throws in test mode on contract violation", () => {
    const invalid = { items: [{ name: "NoId" }] };
    expect(() =>
      validateResponse(PaginatedAgentsSchema, invalid, "GET /agents"),
    ).toThrow();
  });

  it("validateResponse returns parsed data on valid input", () => {
    const valid = { items: [makeAgent()], nextCursor: "abc" };
    const result = validateResponse(PaginatedAgentsSchema, valid, "GET /agents");
    expect(result.items[0].agentId).toBe("agent-001");
    expect(result.nextCursor).toBe("abc");
  });
});
