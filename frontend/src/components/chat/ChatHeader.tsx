import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { History, Clock, Trash2, Plus, Download, Code2 } from "lucide-react";
import { MODEL_GROUPS, findModelLabel } from "../../lib/models";
import type { ChatSession } from "../../stores/chat-store";
import IntegrationTab from "../agents/IntegrationTab";
import { formatDate, formatTimeShort } from "../../lib/date-format";
import useMetaAgentStatus from "../../hooks/useMetaAgentStatus";
import useKiroModels from "../../hooks/useKiroModels";

// Map AgentCore runtime statuses → dot color. READY is the happy path;
// transient provisioning states are yellow; explicit failure is red;
// anything we don't recognize stays gray rather than lying green.
function statusDotColor(status: string | undefined): string {
  switch (status) {
    case "READY":
      return "bg-green-500";
    case "CREATING":
    case "UPDATING":
      return "bg-yellow-500";
    case "CREATE_FAILED":
    case "UPDATE_FAILED":
    case "DELETE_FAILED":
    case "FAILED":
      return "bg-red-500";
    default:
      return "bg-gray-400";
  }
}

interface ChatHeaderProps {
  agentId?: string;
  agentName: string | null;
  selectedModel: string;
  onModelChange: (id: string) => void;
  sessions: ChatSession[];
  activeSessionId: string | null;
  onLoadSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onNewSession: () => void;
  messages: { role: string; content: string }[];
}

export default function ChatHeader({
  agentId, agentName, selectedModel, onModelChange,
  sessions, activeSessionId, onLoadSession, onDeleteSession, onNewSession, messages,
}: ChatHeaderProps) {
  const { t } = useTranslation();
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showMetaIntegration, setShowMetaIntegration] = useState(false);
  // Only poll when the user is actually on the Meta-Agent chat; the hook
  // runs unconditionally but we just don't render the dot for agents.
  const metaStatus = useMetaAgentStatus();
  const kiroModels = useKiroModels();
  const historyRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showHistory && !showModelPicker) return;
    const handler = (e: MouseEvent) => {
      if (showHistory && historyRef.current && !historyRef.current.contains(e.target as Node)) setShowHistory(false);
      if (showModelPicker && modelPickerRef.current && !modelPickerRef.current.contains(e.target as Node)) setShowModelPicker(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showHistory, showModelPicker]);

  // Meta-Agent chat uses Kiro's dynamic model list (Kiro-native IDs);
  // agent chat uses the static Bedrock inference-profile list. The
  // two id formats aren't interchangeable — keep them separate.
  const isMetaAgent = !agentId;
  const selectedModelLabel = isMetaAgent
    ? (kiroModels.find((m) => m.id === selectedModel)?.name || selectedModel || "Select")
    : findModelLabel(selectedModel);

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate flex items-center gap-1.5">
          {!agentId && (
            <span
              className={`inline-block w-2 h-2 rounded-full ${statusDotColor(metaStatus?.status)}`}
              title={metaStatus?.status || "unknown"}
              aria-label={`Meta-Agent status: ${metaStatus?.status || "unknown"}`}
            />
          )}
          {agentId ? agentName : t("agents.metaAgent")}
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
          {agentId ? t("chat.chattingWith", { name: agentName }) : t("chat.metaSubtitle")}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {/* Meta-Agent A2A integration */}
        {!agentId && (
          <button
            type="button"
            onClick={() => setShowMetaIntegration(true)}
            data-testid="meta-a2a-open"
            className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
            title="A2A"
          >
            <Code2 className="w-3 h-3" /> A2A
          </button>
        )}
        {/* Model selector */}
        <div className="relative" ref={modelPickerRef}>
          <button
            onClick={() => setShowModelPicker(!showModelPicker)}
            className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 flex items-center gap-1"
          >
            {selectedModelLabel}
            <svg className="w-3 h-3 text-gray-400 dark:text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
          </button>
          {showModelPicker && (
            <div className="absolute right-0 top-full mt-1 w-48 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-80 overflow-y-auto">
              {isMetaAgent
                ? kiroModels.map((m) => (
                    <button
                      key={m.id}
                      onClick={() => { onModelChange(m.id); setShowModelPicker(false); }}
                      className={`w-full text-left px-3 py-1 text-[10px] hover:bg-blue-50 dark:hover:bg-blue-900/30 ${selectedModel === m.id ? "text-blue-600 dark:text-blue-400 bg-blue-50/50 dark:bg-blue-900/20" : "text-gray-700 dark:text-gray-300"}`}
                    >
                      {m.name || m.id}
                    </button>
                  ))
                : MODEL_GROUPS.map((group) => (
                    <div key={group.label}>
                      <div className="px-2 py-1 text-[9px] font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wide bg-gray-50 dark:bg-gray-800">{group.label}</div>
                      {group.models.map((m) => (
                        <button
                          key={m.id}
                          onClick={() => { onModelChange(m.id); setShowModelPicker(false); }}
                          className={`w-full text-left px-3 py-1 text-[10px] hover:bg-blue-50 dark:hover:bg-blue-900/30 ${selectedModel === m.id ? "text-blue-600 dark:text-blue-400 bg-blue-50/50 dark:bg-blue-900/20" : "text-gray-700 dark:text-gray-300"}`}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                  ))}
            </div>
          )}
        </div>
        {/* Session history */}
        <div className="relative" ref={historyRef}>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={`p-1.5 rounded-lg ${showHistory ? "text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30" : "text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30"}`}
            title={t("chat.sessionHistory")}
          >
            <History className="w-4 h-4" />
          </button>
          {showHistory && (
            <div className="absolute right-0 top-full mt-1 w-72 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-80 overflow-y-auto">
              <div className="p-2 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                <span className="text-xs font-medium text-gray-500 dark:text-gray-400">{t("chat.history")}</span>
                <span className="text-xs text-gray-400 dark:text-gray-500">{t("chat.sessionCount", { count: sessions.length })}</span>
              </div>
              {sessions.length === 0 ? (
                <div className="p-4 text-center text-xs text-gray-400 dark:text-gray-500">{t("chat.noSessions")}</div>
              ) : (
                sessions.map((session) => (
                  <div
                    key={session.id}
                    className={`flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer group ${session.id === activeSessionId ? "bg-blue-50 dark:bg-blue-900/30" : ""}`}
                    onClick={() => { onLoadSession(session.id); setShowHistory(false); }}
                  >
                    <Clock className="w-3 h-3 text-gray-300 dark:text-gray-600 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-gray-700 dark:text-gray-300 truncate">{session.title}</p>
                      <p className="text-xs text-gray-400 dark:text-gray-500">
                        {formatDate(session.updatedAt, "")} {formatTimeShort(session.updatedAt, "")}
                        {" · "}{t("chat.msgCount", { count: session.messages.length })}
                      </p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                      className="p-1 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                      title={t("chat.deleteSession")}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        {/* Export */}
        {messages.length > 0 && (
          <button
            onClick={() => {
              const agentNameExport = agentName || t("agents.metaAgent");
              const lines = [`# ${agentNameExport}\n`];
              for (const msg of messages) {
                if (!msg.content) continue;
                if (msg.role === "user") lines.push(`**User:**\n${msg.content}\n`);
                else if (msg.role === "assistant") lines.push(`**${agentNameExport}:**\n${msg.content}\n`);
              }
              const md = lines.join("\n");
              const blob = new Blob([md], { type: "text/markdown" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `${agentNameExport}-${new Date().toISOString().slice(0, 10)}.md`;
              a.click();
              URL.revokeObjectURL(url);
            }}
            className="p-1.5 text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30"
            title={t("chat.exportMarkdown")}
          >
            <Download className="w-4 h-4" />
          </button>
        )}
        {/* New session */}
        <button onClick={onNewSession} className="p-1.5 text-gray-400 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30" title={t("chat.newSession")}>
          <Plus className="w-4 h-4" />
        </button>
      </div>

      {showMetaIntegration && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
          onClick={() => setShowMetaIntegration(false)}
          data-testid="meta-integration-modal"
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg w-[720px] max-w-full max-h-[85vh] overflow-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <IntegrationTab agentId="meta-agent" kind="meta-agent" />
          </div>
        </div>
      )}
    </div>
  );
}
