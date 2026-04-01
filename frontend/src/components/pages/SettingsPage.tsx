import { Settings, User, Download, Trash2, Database, Shield } from "lucide-react";
import { useUISettings } from "../../stores/ui-settings-store";

export default function SettingsPage() {
  const { sidebarWidth, inputHeight, setSidebarWidth, setInputHeight } = useUISettings();

  const clearLocalStorage = () => {
    if (window.confirm("Clear all local data? This will reset chat history, sessions, and UI settings.")) {
      localStorage.clear();
      window.location.reload();
    }
  };

  const exportAgents = () => {
    // Export all agent configs from localStorage sessions
    const chatData = localStorage.getItem("agent-studio-chat");
    const uiData = localStorage.getItem("agent-studio-ui");
    const blob = new Blob([JSON.stringify({ chat: chatData, ui: uiData }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `agent-studio-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-2xl mx-auto p-6">
      <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2 mb-5">
        <Settings className="w-4 h-4" /> Settings
      </h2>

      <div className="space-y-4">
        {/* UI Preferences */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3">Interface</h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-medium text-gray-700">Sidebar width</p>
                <p className="text-[10px] text-gray-400">Current: {sidebarWidth}px</p>
              </div>
              <button
                onClick={() => setSidebarWidth(224)}
                className="text-[10px] px-2 py-1 border border-gray-200 rounded text-gray-500 hover:bg-gray-50"
              >
                Reset to default
              </button>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-medium text-gray-700">Input area height</p>
                <p className="text-[10px] text-gray-400">Current: {inputHeight}px</p>
              </div>
              <button
                onClick={() => setInputHeight(44)}
                className="text-[10px] px-2 py-1 border border-gray-200 rounded text-gray-500 hover:bg-gray-50"
              >
                Reset to default
              </button>
            </div>
          </div>
        </section>

        {/* Profile */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <User className="w-3.5 h-3.5" /> Account
          </h3>
          <p className="text-xs text-gray-500">Managed by Amazon Cognito. Sign out from the top bar to switch accounts.</p>
        </section>

        {/* Infrastructure Info */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5" /> Infrastructure
          </h3>
          <div className="space-y-1.5 text-xs text-gray-500">
            <div className="flex justify-between">
              <span>Region</span>
              <span className="font-mono text-gray-700">{import.meta.env.VITE_AGENTCORE_REGION || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Storage</span>
              <span className="font-mono text-gray-700">S3 + DynamoDB</span>
            </div>
            <div className="flex justify-between">
              <span>Auth</span>
              <span className="font-mono text-gray-700">Cognito</span>
            </div>
          </div>
        </section>

        {/* Data */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5" /> Data
          </h3>
          <div className="flex gap-2">
            <button
              onClick={exportAgents}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
            >
              <Download className="w-3.5 h-3.5" /> Export local data
            </button>
            <button
              onClick={clearLocalStorage}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 rounded-lg text-red-600 hover:bg-red-50"
            >
              <Trash2 className="w-3.5 h-3.5" /> Clear local data
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
