import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { RouterProvider, createMemoryRouter, useParams, Outlet } from "react-router";

// Stub the heavy layout/shell components so the test only asserts routing shape.
vi.mock("../components/layout/AppShell", () => ({
  default: function AppShellStub() {
    return <Outlet />;
  },
}));
vi.mock("../components/layout/AgentsLayout", () => ({
  default: function AgentsLayoutStub() {
    return <Outlet />;
  },
}));

// Replace the real detail page with a marker that reports the params it received.
vi.mock("../components/pages/AgentDetailPage", () => ({
  default: function Marker() {
    const params = useParams();
    return (
      <div data-testid="detail-marker">
        {params.agentId ?? "-"}|{params.runId ?? "-"}
      </div>
    );
  },
}));

// Other page components are imported by the router but never rendered in these tests.
// We still stub them to avoid pulling heavy deps.
vi.mock("../components/chat/ChatPanel", () => ({ default: () => null }));
vi.mock("../components/agents/AgentEditForm", () => ({ default: () => null }));
vi.mock("../components/pages/SkillsPage", () => ({ default: () => null }));
vi.mock("../components/pages/SkillDetail", () => ({ default: () => null }));
vi.mock("../components/pages/ToolLibraryPage", () => ({ default: () => null }));
vi.mock("../components/pages/ToolDetail", () => ({ default: () => null }));
vi.mock("../components/pages/McpPage", () => ({ default: () => null }));
vi.mock("../components/pages/McpPolicyPage", () => ({ default: () => null }));
vi.mock("../components/pages/MarketplacePage", () => ({ default: () => null }));
vi.mock("../components/pages/CostsPage", () => ({ default: () => null }));
vi.mock("../components/pages/SettingsPage", () => ({ default: () => null }));
vi.mock("../components/common/PageErrorBoundary", () => ({
  default: function ErrBoundaryStub({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
  },
}));

import { createRoutes } from "../router";

describe("router: agent detail deep-links", () => {
  it("resolves /agents/:agentId to AgentDetailPage with agentId param", () => {
    const router = createMemoryRouter(createRoutes(), { initialEntries: ["/agents/agt-1"] });
    render(<RouterProvider router={router} />);
    expect(screen.getByTestId("detail-marker")).toHaveTextContent("agt-1|-");
  });

  it("resolves /agents/:agentId/runs/:runId to AgentDetailPage with both params", () => {
    const router = createMemoryRouter(createRoutes(), {
      initialEntries: ["/agents/agt-1/runs/sched-daily-2026-04-20T08%3A00%3A00Z"],
    });
    render(<RouterProvider router={router} />);
    expect(screen.getByTestId("detail-marker")).toHaveTextContent(
      "agt-1|sched-daily-2026-04-20T08:00:00Z",
    );
  });
});
