import { Authenticator, useAuthenticator } from "@aws-amplify/ui-react";
import "@aws-amplify/ui-react/styles.css";
import IconNav from "./components/layout/IconNav";
import AgentList from "./components/agents/AgentList";
import AgentEditForm from "./components/agents/AgentEditForm";
import ChatPanel from "./components/chat/ChatPanel";
import SkillsPage from "./components/pages/SkillsPage";
import McpPage from "./components/pages/McpPage";
import SettingsPage from "./components/pages/SettingsPage";
import { useNavStore } from "./stores/nav-store";
import { useAgentEditStore } from "./stores/agent-edit-store";
import { useUISettings } from "./stores/ui-settings-store";
import { useCallback, useRef } from "react";

function MainContent() {
  const { activeSection } = useNavStore();
  const { editingAgentId } = useAgentEditStore();

  if (activeSection === "skills") return <SkillsPage />;
  if (activeSection === "mcp") return <McpPage />;
  if (activeSection === "settings") return <SettingsPage />;

  // agents section: resizable sidebar + (edit form or chat)
  const { sidebarWidth, setSidebarWidth } = useUISettings();
  const dragging = useRef(false);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startW = sidebarWidth;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const newW = Math.min(Math.max(startW + ev.clientX - startX, 56), 400);
      setSidebarWidth(newW);
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sidebarWidth]);

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside style={{ width: sidebarWidth }} className="border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 flex-shrink-0 overflow-hidden">
        <AgentList collapsed={sidebarWidth < 100} />
      </aside>
      <div
        onMouseDown={onDragStart}
        onDoubleClick={() => setSidebarWidth(sidebarWidth > 100 ? 56 : 224)}
        className="w-1 cursor-col-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 flex-shrink-0 transition-colors"
        title="Drag to resize, double-click to toggle"
      />
      <main className="flex-1 overflow-hidden">
        {editingAgentId ? <AgentEditForm /> : <ChatPanel />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Authenticator
      components={{
        Header() {
          return (
            <div className="flex flex-col items-center pt-12 pb-6">
              <div className="w-16 h-16 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center mb-4 shadow-lg animate-[fadeSlideIn_0.5s_ease-out]">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" />
                  <path d="M2 17l10 5 10-5" />
                  <path d="M2 12l10 5 10-5" />
                </svg>
              </div>
              <h1 className="text-2xl font-bold text-gray-900 animate-[fadeSlideIn_0.5s_ease-out_0.1s_both]">Agent Studio</h1>
              <p className="text-sm text-gray-500 mt-1 animate-[fadeSlideIn_0.5s_ease-out_0.2s_both]">Build and manage AI agents</p>
            </div>
          );
        },
        Footer() {
          return (
            <div className="text-center py-4 text-[11px] text-gray-400">
              Powered by AWS Bedrock AgentCore
            </div>
          );
        },
      }}
    >
      {({ signOut, user }) => (
        <div className="h-screen flex flex-col bg-white dark:bg-gray-950">
          {/* Top bar */}
          <header className="flex items-center justify-between px-4 py-2 bg-gray-900 text-white">
            <div className="flex items-center gap-2">
              <img src="/logo.svg" alt="Agent Studio" className="w-5 h-5" />
              <span className="font-semibold text-sm">Agent Studio</span>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="text-gray-400">{user?.signInDetails?.loginId}</span>
              <button
                onClick={signOut}
                className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600"
              >
                Sign out
              </button>
            </div>
          </header>

          {/* Body: icon nav + content */}
          <div className="flex flex-1 overflow-hidden">
            <IconNav />
            <MainContent />
          </div>
        </div>
      )}
    </Authenticator>
  );
}
