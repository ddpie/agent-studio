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

  // Traces
  listTraces: vi.fn().mockResolvedValue([]),
  getSessionTrace: vi.fn().mockResolvedValue(null),
  getSessionOutput: vi.fn().mockResolvedValue({
    sessionId: "s-1",
    output: "",
    hasOutput: false,
    metrics: { model: null, inputTokens: null, outputTokens: null, totalTokens: null, durationMs: null, status: "OK" },
  }),
  fetchTraceStats: vi.fn().mockResolvedValue({
    range: "24h",
    count: 0,
    errorCount: 0,
    errorRate: 0,
    latencyMs: { p50: null, p90: null, p95: null, p99: null, avg: null },
    timeseries: [],
  }),

  // Evaluations
  listAgentEvaluations: vi.fn().mockResolvedValue([]),
  enableAgentEvaluations: vi.fn().mockResolvedValue({ configName: "", status: "ALREADY_EXISTS" }),
  getAgentEvaluationStatus: vi.fn().mockResolvedValue({
    exists: false,
    configName: "",
    status: null,
    executionStatus: null,
  }),

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

const mockStore = { currentWorkspace: { workspaceId: "ws-1", role: "viewer" as const } };
vi.mock("../../../stores/workspace-store", () => ({
  useWorkspaceStore: () => mockStore,
}));

import AgentDetailPage from "../AgentDetailPage";

function renderPage(initialPath: string = "/agents/agt-1") {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          <Route path="/agents/:agentId/runs/:sessionId" element={<AgentDetailPage />} />
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

  it("renders Evaluations section", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("evaluations-tab")).toBeInTheDocument();
  });

  it("renders Runs section (formerly Traces)", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("runs-section")).toBeInTheDocument();
    expect(await screen.findByTestId("traces-tab")).toBeInTheDocument(); // inner TracesTab testid preserved
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

  it("pre-selects a run when routed to /agents/:id/runs/:sessionId", async () => {
    renderPage("/agents/agt-1/runs/sched-daily-manual-123");
    await screen.findByTestId("agent-detail-title");
    // When a sessionId is pre-selected, the "select a session" placeholder is hidden.
    expect(screen.queryByText(/select a session/i)).not.toBeInTheDocument();
  });
});
