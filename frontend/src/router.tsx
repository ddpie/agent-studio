import { createHashRouter, Navigate } from "react-router";
import AppShell from "./components/layout/AppShell";
import AgentsLayout from "./components/layout/AgentsLayout";
import ChatPanel from "./components/chat/ChatPanel";
import AgentEditForm from "./components/agents/AgentEditForm";
import SkillsPage from "./components/pages/SkillsPage";
import SkillDetail from "./components/pages/SkillDetail";
import ToolLibraryPage from "./components/pages/ToolLibraryPage";
import ToolDetail from "./components/pages/ToolDetail";
import McpPage from "./components/pages/McpPage";
import SettingsPage from "./components/pages/SettingsPage";

export function createRouter(
  signOut?: () => void,
  user?: { signInDetails?: { loginId?: string } }
) {
  return createHashRouter([
    {
      path: "/",
      element: <AppShell signOut={signOut} user={user} />,
      children: [
        { index: true, element: <Navigate to="/agents" replace /> },
        {
          path: "agents",
          element: <AgentsLayout />,
          children: [
            { index: true, element: <ChatPanel /> },
            { path: "chat/:agentId", element: <ChatPanel /> },
            { path: "edit/:agentId", element: <AgentEditForm /> },
            { path: "edit/:agentId/skills/:skillId", element: <AgentEditForm /> },
          ],
        },
        { path: "skills", element: <SkillsPage /> },
        { path: "skills/:skillId", element: <SkillDetail /> },
        { path: "tools", element: <ToolLibraryPage /> },
        { path: "tools/:toolId", element: <ToolDetail /> },
        { path: "mcp", element: <McpPage /> },
        { path: "settings", element: <SettingsPage /> },
      ],
    },
  ]);
}
