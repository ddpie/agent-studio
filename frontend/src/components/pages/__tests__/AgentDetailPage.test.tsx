import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { I18nextProvider } from "react-i18next";
import i18n from "../../../i18n";

vi.mock("../../../lib/api-client", () => ({
  fetchAgent: vi.fn().mockResolvedValue({
    agentId: "agt-1",
    name: "testAgent",
    workspace_id: "ws-1",
  }),
  listAgentEvaluations: vi.fn().mockResolvedValue([]),
  listTraces: vi.fn().mockResolvedValue([]),
  getSessionTrace: vi.fn().mockResolvedValue(null),
  listAgentVersions: vi.fn().mockResolvedValue([]),
  listAgentEndpoints: vi.fn().mockResolvedValue([]),
  getAgentRuntime: vi.fn().mockResolvedValue({ status: "READY" }),
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
}));

const mockStore = { currentWorkspace: { workspaceId: "ws-1", role: "viewer" as const } };
vi.mock("../../../stores/workspace-store", () => ({
  useWorkspaceStore: () => mockStore,
}));

import AgentDetailPage from "../AgentDetailPage";

function renderPage() {
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter initialEntries={["/agents/agt-1"]}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
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

  it("renders Traces section", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("traces-tab")).toBeInTheDocument();
  });

  it("renders Integration section with card/endpoint URLs", async () => {
    renderPage();
    await screen.findByTestId("agent-detail-title");
    expect(await screen.findByTestId("integration-tab")).toBeInTheDocument();
    expect(await screen.findByTestId("card-url-value")).toHaveTextContent(/well-known\/agent-card\.json/);
    expect(await screen.findByTestId("endpoint-url-value")).toHaveTextContent(/\/a2a\/agents\//);
  });
});
