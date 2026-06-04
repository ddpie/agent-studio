import { http, HttpResponse } from "msw";

/**
 * MSW request handlers for critical API endpoints.
 * Used in contract tests to simulate backend responses.
 */

const BASE = "https://test-api.example.com";
const WS_ID = "ws-test-123";

// ── Mock data factories ──

export function makeAgent(overrides: Record<string, unknown> = {}) {
  return {
    agentId: "agent-001",
    name: "TestAgent",
    display_name: "Test Agent",
    description: "A test agent for contract testing",
    status: "READY",
    visibility: "private",
    model_id: "us.anthropic.claude-sonnet-4-20250514-v1:0",
    created_at: "2026-01-15T10:00:00Z",
    updated_at: "2026-01-16T12:00:00Z",
    runtime_type: "zip" as const,
    ...overrides,
  };
}

export function makeRunSummary(overrides: Record<string, unknown> = {}) {
  return {
    runId: "run-001",
    trigger: "manual" as const,
    scheduleId: null,
    status: "completed" as const,
    input: "Hello, analyze this data",
    model: "us.anthropic.claude-sonnet-4-20250514-v1:0",
    totalTokens: 1500,
    durationMs: 3200,
    artifactCount: 0,
    startedAt: "2026-01-15T10:00:00Z",
    completedAt: "2026-01-15T10:00:03Z",
    ...overrides,
  };
}

export function makeWorkspaceDetail(overrides: Record<string, unknown> = {}) {
  return {
    workspaceId: WS_ID,
    name: "Test Workspace",
    description: "A test workspace",
    owner_id: "user-owner-001",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-10T00:00:00Z",
    memory_id: "mem-001",
    members: [
      {
        userId: "user-owner-001",
        role: "owner" as const,
        joined_at: "2026-01-01T00:00:00Z",
        display_name: "Owner User",
        email: "owner@test.com",
      },
      {
        userId: "user-editor-002",
        role: "editor" as const,
        joined_at: "2026-01-02T00:00:00Z",
        display_name: "Editor User",
      },
    ],
    ...overrides,
  };
}

export function makeMemoryRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "mem-rec-001",
    content: { text: "User prefers dark mode" },
    createdAt: 1705312800000,
    namespace: "preferences",
    ...overrides,
  };
}

// ── Handlers ──

export const handlers = [
  // GET /api/workspaces/{wsId}/agents - list agents
  http.get(`${BASE}/api/workspaces/:wsId/agents`, ({ request }) => {
    const url = new URL(request.url);
    const limit = Number(url.searchParams.get("limit") || "50");
    const items = Array.from({ length: Math.min(limit, 3) }, (_, i) =>
      makeAgent({ agentId: `agent-${String(i + 1).padStart(3, "0")}`, name: `Agent${i + 1}` }),
    );
    return HttpResponse.json({ items, nextCursor: undefined });
  }),

  // GET /api/workspaces/{wsId}/agents/:agentId - single agent
  http.get(`${BASE}/api/workspaces/:wsId/agents/:agentId`, ({ params }) => {
    return HttpResponse.json(
      makeAgent({ agentId: params.agentId as string }),
    );
  }),

  // GET /api/workspaces/{wsId}/agents/:agentId/runs - list runs
  http.get(`${BASE}/api/workspaces/:wsId/agents/:agentId/runs`, () => {
    const runs = [
      makeRunSummary({ runId: "run-001", status: "completed" }),
      makeRunSummary({ runId: "run-002", status: "running", completedAt: null, durationMs: null }),
      makeRunSummary({ runId: "run-003", trigger: "schedule", scheduleId: "sched-001", status: "failed" }),
    ];
    return HttpResponse.json({ runs });
  }),

  // GET /api/workspaces/{wsId} - workspace detail
  http.get(`${BASE}/api/workspaces/:wsId`, () => {
    return HttpResponse.json(makeWorkspaceDetail());
  }),

  // GET /api/workspaces - workspace list
  http.get(`${BASE}/api/workspaces`, () => {
    return HttpResponse.json({
      items: [
        { workspaceId: WS_ID, name: "Test Workspace", role: "owner", created_at: "2026-01-01T00:00:00Z" },
        { workspaceId: "ws-other-456", name: "Other Workspace", role: "editor" },
      ],
    });
  }),

  // GET /api/workspaces/{wsId}/agents/:agentId/my-memories - memory bundle
  http.get(`${BASE}/api/workspaces/:wsId/agents/:agentId/my-memories`, () => {
    return HttpResponse.json({
      preferences: {
        records: [makeMemoryRecord({ id: "pref-001", namespace: "preferences" })],
        nextToken: null,
      },
      facts: {
        records: [makeMemoryRecord({ id: "fact-001", namespace: "facts", content: { text: "User is a developer" } })],
        nextToken: "token-facts-page2",
      },
      summaries: { records: [], nextToken: null },
      episodes: { records: [], nextToken: null },
    });
  }),
];

export { BASE, WS_ID };
