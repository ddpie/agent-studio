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
            <div className="flex flex-col items-center pt-16 pb-6">
              <div className="relative mb-6 animate-[fadeSlideIn_0.6s_ease-out]">
                <div className="w-20 h-20 rounded-2xl shadow-xl shadow-blue-500/20 overflow-hidden">
                  <img src="/logo.svg" alt="Agent Studio" className="w-full h-full" />
                </div>
                {/* Animated orbit dots representing agents */}
                <div className="absolute -inset-3 animate-[spin_8s_linear_infinite]">
                  <div className="absolute top-0 left-1/2 -translate-x-1/2 w-2.5 h-2.5 bg-blue-400 rounded-full shadow-lg shadow-blue-400/50" />
                </div>
                <div className="absolute -inset-5 animate-[spin_12s_linear_infinite_reverse]">
                  <div className="absolute bottom-0 right-0 w-2 h-2 bg-purple-400 rounded-full shadow-lg shadow-purple-400/50" />
                </div>
                <div className="absolute -inset-4 animate-[spin_10s_linear_infinite]">
                  <div className="absolute top-1/2 right-0 w-2 h-2 bg-indigo-400 rounded-full shadow-lg shadow-indigo-400/50" />
                </div>
                <div className="absolute -inset-6 animate-[spin_15s_linear_infinite]">
                  <div className="absolute top-1/4 left-0 w-1.5 h-1.5 bg-cyan-400 rounded-full shadow-lg shadow-cyan-400/50" />
                </div>
                <div className="absolute -inset-7 animate-[spin_18s_linear_infinite_reverse]">
                  <div className="absolute bottom-1/4 right-1/4 w-1.5 h-1.5 bg-emerald-400 rounded-full shadow-lg shadow-emerald-400/50" />
                </div>
                <div className="absolute -inset-8 animate-[spin_20s_linear_infinite]">
                  <div className="absolute top-0 right-1/3 w-1 h-1 bg-amber-400 rounded-full shadow-lg shadow-amber-400/50" />
                </div>
                <div className="absolute -inset-5 animate-[spin_14s_linear_infinite_reverse]">
                  <div className="absolute bottom-0 left-1/4 w-1 h-1 bg-rose-400 rounded-full shadow-lg shadow-rose-400/50" />
                </div>
              </div>
              <h1 className="text-2xl font-bold text-gray-900 animate-[fadeSlideIn_0.6s_ease-out_0.15s_both]">Agent Studio</h1>
              <p className="text-sm text-gray-500 mt-1 animate-[fadeSlideIn_0.6s_ease-out_0.25s_both]">Build and orchestrate AI agents on AWS</p>
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
