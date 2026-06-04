import { lazy, Suspense, type ReactNode } from "react";
import { createHashRouter, Navigate } from "react-router";
import AppShell from "./components/layout/AppShell";
import AgentsLayout from "./components/layout/AgentsLayout";
import ChatPanel from "./components/chat/ChatPanel";
import PageErrorBoundary from "./components/common/PageErrorBoundary";

// Route-level code splitting — heavy pages loaded on demand
const AgentEditForm = lazy(() => import("./components/agents/AgentEditForm"));
const AgentDetailPage = lazy(() => import("./components/pages/AgentDetailPage"));
const SkillsPage = lazy(() => import("./components/pages/SkillsPage"));
const SkillDetail = lazy(() => import("./components/pages/SkillDetail"));
const ToolLibraryPage = lazy(() => import("./components/pages/ToolLibraryPage"));
const ToolDetail = lazy(() => import("./components/pages/ToolDetail"));
const KBList = lazy(() => import("./components/kb/KBList"));
const KBDetail = lazy(() => import("./components/kb/KBDetail"));
const McpPage = lazy(() => import("./components/pages/McpPage"));
const McpPolicyPage = lazy(() => import("./components/pages/McpPolicyPage"));
const MarketplacePage = lazy(() => import("./components/pages/MarketplacePage"));
const CostsPage = lazy(() => import("./components/pages/CostsPage"));
const SettingsPage = lazy(() => import("./components/pages/SettingsPage"));
const AdminConsolePage = lazy(() => import("./components/pages/AdminConsolePage"));

function LazyFallback() {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="w-5 h-5 border-2 border-gray-300 dark:border-gray-600 border-t-blue-500 rounded-full animate-spin" />
    </div>
  );
}

function withBoundary(element: ReactNode) {
  return <PageErrorBoundary><Suspense fallback={<LazyFallback />}>{element}</Suspense></PageErrorBoundary>;
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
        { path: "knowledge-bases", element: withBoundary(<KBList />) },
        { path: "knowledge-bases/:kbId", element: withBoundary(<KBDetail />) },
        { path: "mcp", element: withBoundary(<McpPage />) },
        { path: "mcp-policy", element: withBoundary(<McpPolicyPage />) },
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
