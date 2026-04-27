import { lazy, type ReactNode } from "react";
import { createHashRouter, Navigate } from "react-router";
import AppShell from "./components/layout/AppShell";
import AgentsLayout from "./components/layout/AgentsLayout";
import ChatPanel from "./components/chat/ChatPanel";
import AgentEditForm from "./components/agents/AgentEditForm";
import AgentDetailPage from "./components/pages/AgentDetailPage";
import SkillsPage from "./components/pages/SkillsPage";
import SkillDetail from "./components/pages/SkillDetail";
import ToolLibraryPage from "./components/pages/ToolLibraryPage";
import ToolDetail from "./components/pages/ToolDetail";
import McpServersPage from "./components/mcp/McpServersPage";
import AdminMcpFleetPage from "./components/admin/AdminMcpFleetPage";
import MarketplacePage from "./components/pages/MarketplacePage";
import CostsPage from "./components/pages/CostsPage";
import SettingsPage from "./components/pages/SettingsPage";
import PageErrorBoundary from "./components/common/PageErrorBoundary";

const AdminConsolePage = lazy(() => import("./components/pages/AdminConsolePage"));

function withBoundary(element: ReactNode) {
  return <PageErrorBoundary>{element}</PageErrorBoundary>;
}

export function createRoutes(
  signOut?: () => void,
  user?: { signInDetails?: { loginId?: string } }
) {
  return [
    {
      path: "/",
      element: <AppShell signOut={signOut} user={user} />,
      children: [
        { index: true, element: <Navigate to="/agents" replace /> },
        {
          path: "agents",
          element: <AgentsLayout />,
          children: [
            { index: true, element: withBoundary(<ChatPanel />) },
            { path: "chat/:agentId", element: withBoundary(<ChatPanel />) },
            { path: "edit/:agentId", element: withBoundary(<AgentEditForm />) },
            { path: "edit/:agentId/skills/:skillId", element: withBoundary(<AgentEditForm />) },
            { path: ":agentId", element: withBoundary(<AgentDetailPage />) },
            { path: ":agentId/runs/:runId", element: withBoundary(<AgentDetailPage />) },
          ],
        },
        { path: "skills", element: withBoundary(<SkillsPage />) },
        { path: "skills/:skillId", element: withBoundary(<SkillDetail />) },
        { path: "tools", element: withBoundary(<ToolLibraryPage />) },
        { path: "tools/:toolId", element: withBoundary(<ToolDetail />) },
        { path: "mcp", element: withBoundary(<McpServersPage />) },
        { path: "admin/mcp-fleet", element: withBoundary(<AdminMcpFleetPage />) },
        { path: "marketplace", element: withBoundary(<MarketplacePage />) },
        { path: "marketplace/:tab", element: withBoundary(<MarketplacePage />) },
        { path: "costs", element: withBoundary(<CostsPage />) },
        { path: "admin", element: withBoundary(<AdminConsolePage />) },
        { path: "settings", element: withBoundary(<SettingsPage />) },
      ],
    },
  ];
}

export function createRouter(
  signOut?: () => void,
  user?: { signInDetails?: { loginId?: string } }
) {
  return createHashRouter(createRoutes(signOut, user));
}
