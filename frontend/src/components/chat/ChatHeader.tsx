import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { History, Clock, Trash2, Plus, Download } from "lucide-react";
import { MODEL_GROUPS, findModelLabel } from "../../lib/models";
import type { ChatSession } from "../../stores/chat-store";

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

  const selectedModelLabel = findModelLabel(selectedModel);

  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
      <div className="min-w-0">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate">
          {agentId ? agentName : t("agents.metaAgent")}
        </h2>
        <p className="text-xs text-gray-500 truncate">
          {agentId ? t("chat.chattingWith", { name: agentName }) : t("chat.metaSubtitle")}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {/* Model selector */}
        <div className="relative" ref={modelPickerRef}>
          <button
            onClick={() => setShowModelPicker(!showModelPicker)}
            className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 flex items-center gap-1"
          >
            {selectedModelLabel}
            <svg className="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
          </button>
          {showModelPicker && (
            <div className="absolute right-0 top-full mt-1 w-48 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-80 overflow-y-auto">
              {MODEL_GROUPS.map((group) => (
                <div key={group.label}>
                  <div className="px-2 py-1 text-[9px] font-medium text-gray-400 uppercase tracking-wide bg-gray-50 dark:bg-gray-800">{group.label}</div>
                  {group.models.map((m) => (
                    <button
                      key={m.id}
                      onClick={() => { onModelChange(m.id); setShowModelPicker(false); }}
                      className={`w-full text-left px-3 py-1 text-[10px] hover:bg-blue-50 ${selectedModel === m.id ? "text-blue-600 bg-blue-50/50" : "text-gray-700 dark:text-gray-300"}`}
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
            className={`p-1.5 rounded-lg ${showHistory ? "text-blue-600 bg-blue-50" : "text-gray-400 hover:text-blue-600 hover:bg-blue-50"}`}
            title={t("chat.sessionHistory")}
          >
            <History className="w-4 h-4" />
          </button>
          {showHistory && (
            <div className="absolute right-0 top-full mt-1 w-72 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 z-50 max-h-80 overflow-y-auto">
              <div className="p-2 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
                <span className="text-xs font-medium text-gray-500">{t("chat.history")}</span>
                <span className="text-xs text-gray-400">{t("chat.sessionCount", { count: sessions.length })}</span>
              </div>
              {sessions.length === 0 ? (
                <div className="p-4 text-center text-xs text-gray-400">{t("chat.noSessions")}</div>
              ) : (
                sessions.map((session) => (
                  <div
                    key={session.id}
                    className={`flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer group ${session.id === activeSessionId ? "bg-blue-50" : ""}`}
                    onClick={() => { onLoadSession(session.id); setShowHistory(false); }}
                  >
                    <Clock className="w-3 h-3 text-gray-300 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-gray-700 dark:text-gray-300 truncate">{session.title}</p>
                      <p className="text-xs text-gray-400">
                        {new Date(session.updatedAt).toLocaleDateString()} {new Date(session.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        {" · "}{t("chat.msgCount", { count: session.messages.length })}
                      </p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                      className="p-1 text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
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
            className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg hover:bg-blue-50"
            title={t("chat.exportMarkdown")}
          >
            <Download className="w-4 h-4" />
          </button>
        )}
        {/* New session */}
        <button onClick={onNewSession} className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg hover:bg-blue-50" title={t("chat.newSession")}>
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
