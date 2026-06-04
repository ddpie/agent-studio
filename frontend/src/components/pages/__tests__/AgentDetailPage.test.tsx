import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";

vi.mock("../../../lib/api-client", () => ({
  // Core
  fetchAgent: vi.fn().mockResolvedValue({
    agentId: "agt-1",
    name: "testAgent",
    workspace_id: "ws-1",
  }),
  publishAgent: vi.fn().mockResolvedValue(undefined),
  unpublishAgent: vi.fn().mockResolvedValue(undefined),

  // Deployments / endpoints / runtime
  listAgentVersions: vi.fn().mockResolvedValue([]),
  listAgentEndpoints: vi.fn().mockResolvedValue([]),
  getAgentRuntime: vi.fn().mockResolvedValue({ status: "READY" }),
  createAgentEndpoint: vi.fn().mockResolvedValue(undefined),
  updateAgentEndpoint: vi.fn().mockResolvedValue(undefined),
  deleteAgentEndpoint: vi.fn().mockResolvedValue(undefined),

  // Logs
  fetchAgentLogs: vi.fn().mockResolvedValue({ events: [], nextToken: null }),

  getSessionOutput: vi.fn().mockResolvedValue({
    sessionId: "s-1",
    output: "",
    hasOutput: false,
    metrics: { model: null, inputTokens: null, outputTokens: null, totalTokens: null, durationMs: null, status: "OK" },
  }),

  // Evaluations
  listAgentEvaluations: vi.fn().mockResolvedValue({ evaluations: [], diagnostics: null }),
  enableAgentEvaluations: vi.fn().mockResolvedValue({ configName: "", status: "ALREADY_EXISTS" }),
  getAgentEvaluationStatus: vi.fn().mockResolvedValue({
    exists: false,
    configName: "",
    status: null,
    executionStatus: null,
  }),

  // Trace stats (StatsStrip) — full TraceStats shape from api-client.ts
  getTraceStats: vi.fn().mockResolvedValue({
    range: "24h",
    count: 0,
    errorCount: 0,
    errorRate: 0,
    latencyMs: { p50: null, p90: null, p95: null, p99: null, avg: null },
    timeseries: [],
  }),
  listTraces: vi.fn().mockResolvedValue([]),
  getSessionTrace: vi.fn().mockResolvedValue({ sessionId: "s-1", spans: [] }),

  // Costs
  fetchAgentCosts: vi.fn().mockResolvedValue({
    range: "24h",
    totalCost: 0,
    totalTokens: 0,
    invocations: 0,
    byModel: [],
    timeseries: [],
  }),

  // Schedules
  listAgentSchedules: vi.fn().mockResolvedValue([]),
  createAgentSchedule: vi.fn().mockResolvedValue({ name: "", suffix: "", cron: "" }),
  updateAgentSchedule: vi.fn().mockResolvedValue(undefined),
  deleteAgentSchedule: vi.fn().mockResolvedValue(undefined),
  runAgentScheduleNow: vi.fn().mockResolvedValue({ sessionId: "", invokedAt: 0 }),
  listScheduleExecutions: vi.fn().mockResolvedValue([]),

  // Secrets
  listAgentSecrets: vi.fn().mockResolvedValue([]),
  putAgentSecret: vi.fn().mockResolvedValue(undefined),
  deleteAgentSecret: vi.fn().mockResolvedValue(undefined),

  // A2A
  listA2aKeys: vi.fn().mockResolvedValue([]),
  createA2aKey: vi.fn(),
  revokeA2aKey: vi.fn(),
  listMetaA2aKeys: vi.fn().mockResolvedValue([]),
  createMetaA2aKey: vi.fn(),
  revokeMetaA2aKey: vi.fn(),
  getPublicAgentCardUrl: (id: string) => `/a2a/agents/${id}/.well-known/agent-card.json`,
  getA2aEndpointUrl: (id: string) => `/a2a/agents/${id}`,
  getMetaA2aCardUrl: () => `/a2a/meta-agent/.well-known/agent-card.json`,
  getMetaA2aEndpointUrl: () => `/a2a/meta-agent`,

  // Misc used by downstream hooks / guards
  ApiError: class ApiError extends Error {
    status: number;
    body: unknown;
    constructor(status: number, body: unknown) {
      super(`${status}`);
      this.status = status;
      this.body = body;
    }
  },
}));

// Schedules' recent-runs list + RunDetailModal both lean on the runs
// client directly, so stub the whole module here to keep tests offline.
vi.mock("../../../lib/runs-client", () => ({
  listRuns: vi.fn().mockResolvedValue({ runs: [], nextToken: undefined }),
  getRun: vi.fn().mockResolvedValue({
    runId: "01JWXYZ-test-run",
    trigger: "schedule",
    scheduleId: null,
    sessionId: null,
    status: "completed",
    input: "",
    outputUrl: null,
    artifactRefs: [],
    usage: { promptTokens: null, completionTokens: null, totalTokens: null },
    durationMs: null,
    model: null,
    error: null,
    startedAt: "2026-04-21T00:00:00Z",
    completedAt: null,
  }),
  fetchRunOutput: vi.fn().mockResolvedValue({ text: "", toolCalls: [] }),
}));

const mockStore = { currentWorkspace: { workspaceId: "ws-1", role: "viewer" as const } };
vi.mock("../../../stores/workspace-store", () => ({
  useWorkspaceStore: () => mockStore,
}));

import AgentDetailPage from "../AgentDetailPage";

function renderPage(initialPath = "/agents/agt-1") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="/agents/:agentId/runs/:runId" element={<AgentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </I18nextProvider>
  );
}

describe("AgentDetailPage", () => {
  beforeEach(() => {
    mockStore.currentWorkspace = { workspaceId: "ws-1", role: "viewer" as const };
  });

  it("renders the agent name as page title", async () => {
    renderPage();
    expect(await screen.findByTestId("agent-detail-title")).toHaveTextContent(/testAgent/);
  });

  it("renders a subtitle under the title", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(screen.getByTestId("agent-detail-subtitle")).toBeInTheDocument();
  });

  it("hides the Edit button for viewer role", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(screen.queryByTestId("edit-agent-btn")).not.toBeInTheDocument();
  });

  it("renders Deployments section", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("deployments-tab")).toBeInTheDocument();
  });

  it("renders Endpoints section simultaneously (linear stack)", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("deployments-tab")).toBeInTheDocument();
    expect(await screen.findByTestId("endpoints-tab")).toBeInTheDocument();
  });

  // NOTE: "renders Evaluations section" was removed. EvaluationsTab is not
  // currently mounted by AgentDetailPage; the evaluations UI was moved out
  // of the per-agent detail page. Restore this test if/when it returns.

  it("renders Schedules section (the runs view now lives inside it)", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("schedules-section")).toBeInTheDocument();
    expect(await screen.findByTestId("schedules-tab")).toBeInTheDocument();
  });

  it("renders Integration section with card/endpoint URLs", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("integration-tab")).toBeInTheDocument();
    expect(await screen.findByTestId("card-url-value")).toHaveTextContent(/well-known\/agent-card\.json/);
    expect(await screen.findByTestId("endpoint-url-value")).toHaveTextContent(/\/a2a\/agents\//);
  });

  it("renders the Advanced group header and keeps Deployments/Endpoints/Secrets/Logs inside it", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("nav-group-advanced")).toBeInTheDocument();
    expect(await screen.findByTestId("deployments-section")).toBeInTheDocument();
    expect(await screen.findByTestId("endpoints-section")).toBeInTheDocument();
    expect(await screen.findByTestId("secrets-section")).toBeInTheDocument();
    expect(await screen.findByTestId("logs-section")).toBeInTheDocument();
  });

  it("opens the run-detail modal when deep-linked via /agents/:id/runs/:runId", async () => {
    renderPage("/agents/agt-1/runs/01JWXYZ-test-run");
    await screen.findByTestId("agent-detail-title");
    // Modal mounts on load; inner content may be Loading but the shell is there.
    expect(await screen.findByTestId("run-detail-modal")).toBeInTheDocument();
  });
});
