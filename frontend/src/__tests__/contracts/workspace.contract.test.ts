import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { setupServer } from "msw/node";
import {
  WorkspaceDetailSchema,
  WorkspaceMemberSchema,
  WorkspaceListResponseSchema,
} from "../../lib/contracts";
import { handlers, BASE, WS_ID, makeWorkspaceDetail } from "../../mocks/handlers";

const server = setupServer(...handlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("Workspace contract schemas", () => {
  it("validates workspace detail from MSW handler", async () => {
    const resp = await fetch(`${BASE}/api/workspaces/${WS_ID}`);
    const data = await resp.json();

    const parsed = WorkspaceDetailSchema.parse(data);
    expect(parsed.workspaceId).toBe(WS_ID);
    expect(parsed.members).toHaveLength(2);
    expect(parsed.members[0].role).toBe("owner");
  });

  it("validates workspace list response", async () => {
    const resp = await fetch(`${BASE}/api/workspaces`);
    const data = await resp.json();

    const parsed = WorkspaceListResponseSchema.parse(data);
    expect(parsed.items).toHaveLength(2);
    expect(parsed.items[0].workspaceId).toBe(WS_ID);
  });

  it("rejects a member with an invalid role", () => {
    const invalid = {
      userId: "user-001",
      role: "superadmin",
      joined_at: "2026-01-01T00:00:00Z",
    };
    const result = WorkspaceMemberSchema.safeParse(invalid);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues[0].path).toContain("role");
    }
  });

  it("rejects workspace detail missing required 'members' array", () => {
    const invalid = { workspaceId: "ws-1", name: "Test" };
    const result = WorkspaceDetailSchema.safeParse(invalid);
    expect(result.success).toBe(false);
  });

  it("accepts workspace detail with minimal optional fields", () => {
    const minimal = makeWorkspaceDetail({
      description: undefined,
      owner_id: undefined,
      memory_id: undefined,
    });
    // Remove optional keys to test absence
    delete (minimal as Record<string, unknown>).description;
    delete (minimal as Record<string, unknown>).owner_id;
    delete (minimal as Record<string, unknown>).memory_id;

    const result = WorkspaceDetailSchema.safeParse(minimal);
    expect(result.success).toBe(true);
  });
});
