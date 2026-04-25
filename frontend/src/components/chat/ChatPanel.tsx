import { useState, useEffect, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useChatStore, useCurrentAgentChat } from "../../stores/chat-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { fetchAgentMetadataLight, type AgentMetadata } from "../../lib/agent-metadata";
import { useUISettings } from "../../stores/ui-settings-store";
import { DEFAULT_MODEL_ID, DEFAULT_KIRO_MODEL_ID } from "../../lib/models";
import "katex/dist/katex.min.css";
import { Link } from "react-router";
import { AlertTriangle } from "lucide-react";
import ChatHeader from "./ChatHeader";
import MessageList from "./MessageList";
import ChatInput, { type ChatInputHandle } from "./ChatInput";
import useKiroKeyStatus from "../../hooks/useKiroKeyStatus";

export default function ChatPanel() {
  const { t } = useTranslation();
  const { agentId } = useParams();
  const {
    sendMessage, cancelStreaming,
    switchAgent, newSession, loadSession, deleteSession,
    getAgentSessions, setSelectedModel: storeSetModel,
    regenerateLastMessage,
  } = useChatStore();
  // Read per-agent state keyed by the URL's agentId (not the store's
  // currentAgentId), otherwise the 1-frame lag between URL change and
  // switchAgent's useEffect causes stale state to leak across routes —
  // e.g. the "calling load_skill" label flashing on another agent's page.
  const {
    messages, isStreaming, statusText, activeTool, autoContinue, activeSessionId, selectedModelId,
  } = useCurrentAgentChat(agentId || null);
  const { agents, fetchAgents } = useAgentListStore();
  const agentName = agents.find(a => a.id === agentId)?.displayName || null;
  // Meta-Agent runs on Kiro (expects Kiro-native model ids like
  // "claude-opus-4.6"). Sub-agents run on Bedrock (expects inference-
  // profile ids like "us.anthropic.claude-opus-4-7"). Without branching
  // the default, a first-time visitor to Meta-Agent ends up sending a
  // Bedrock id to Kiro, which either fails or silently falls back.
  const defaultModel = agentId ? DEFAULT_MODEL_ID : DEFAULT_KIRO_MODEL_ID;
  const selectedModel = selectedModelId || defaultModel;
  // Only fetch kiro key status on the Meta-Agent route. Sub-agent chats
  // don't use Kiro so the banner would be a distraction.
  const kiroKey = useKiroKeyStatus();
  const showKiroBanner = !agentId && kiroKey && !kiroKey.configured;
  const setSelectedModel = (id: string) => storeSetModel(id);
  const [metadata, setMetadata] = useState<AgentMetadata | null>(null);
  const { inputHeight, setInputHeight } = useUISettings();
  const prevStreamingRef = useRef(false);
  const chatInputRef = useRef<ChatInputHandle>(null);

  const imagesAllowed = !agentId || metadata?.supports_images === true;
  const agentSessions = getAgentSessions();
  const sentMessages = messages.filter((m) => m.role === "user" && m.content).map((m) => m.content);

  const onInputDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = inputHeight;
    const onMove = (ev: MouseEvent) => setInputHeight(Math.min(Math.max(startH - (ev.clientY - startY), 44), 400));
    const onUp = () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [inputHeight, setInputHeight]);

  // Refresh agent list when streaming finishes
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) fetchAgents();
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, fetchAgents]);

  // Sync URL agent to store
  useEffect(() => { switchAgent(agentId || null, agentName); }, [agentId, agentName, switchAgent]);

  // Load agent metadata
  useEffect(() => {
    if (agentName && agentId) {
      fetchAgentMetadataLight(agentId).then((m) => {
        setMetadata(m);
        if (m?.default_model_id) setSelectedModel(m.default_model_id);
      });
    } else {
      setMetadata(null);
    }
  }, [agentId, agentName]);

  const emptyState = (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500">
      <p className="text-4xl mb-4">{agentId ? "💬" : "🤖"}</p>
      <p className="text-lg font-medium text-gray-600 dark:text-gray-400">
        {metadata?.welcome_message || (agentId ? t("chat.chatWith", { name: agentName }) : t("chat.welcomeMeta"))}
      </p>
      {!agentId && <p className="text-sm mt-1">{t("chat.metaSubtitle")}</p>}
      <div className="mt-6 grid gap-2 text-sm w-full max-w-lg">
        {(metadata?.suggestions || (!agentId ? [t("chat.defaultSuggestion1"), t("chat.defaultSuggestion2"), t("chat.defaultSuggestion3")] : [])).map((suggestion) => (
          <button key={suggestion} onClick={() => chatInputRef.current?.setInput(suggestion)} className="text-left px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400">
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-full">
      <ChatHeader
        agentId={agentId}
        agentName={agentName}
        selectedModel={selectedModel}
        onModelChange={setSelectedModel}
        sessions={agentSessions}
        activeSessionId={activeSessionId}
        onLoadSession={loadSession}
        onDeleteSession={deleteSession}
        onNewSession={newSession}
        messages={messages}
      />
      {showKiroBanner && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 dark:bg-amber-950/40 border-b border-amber-200 dark:border-amber-900/60 text-[12px] text-amber-800 dark:text-amber-200">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">
            {t("kiroKey.bannerMessage", "Kiro API key is not configured for this workspace. Meta-Agent will not respond until an admin adds one.")}
          </span>
          <Link to="/settings?tab=workspace" className="underline hover:no-underline font-medium">
            {t("kiroKey.bannerCta", "Open Workspace Settings")}
          </Link>
        </div>
      )}
      <MessageList
        messages={messages}
        isStreaming={isStreaming}
        statusText={statusText}
        activeTool={activeTool}
        autoContinue={autoContinue}
        onRegenerate={regenerateLastMessage}
        emptyState={emptyState}
      />
      <ChatInput
        ref={chatInputRef}
        onSend={sendMessage}
        isStreaming={isStreaming}
        onCancel={cancelStreaming}
        imagesAllowed={imagesAllowed}
        selectedModel={selectedModel}
        inputHeight={inputHeight}
        dragHandleProps={{ onMouseDown: onInputDragStart, onDoubleClick: () => setInputHeight(inputHeight > 60 ? 44 : 160) }}
        sentMessages={sentMessages}
        activeSessionId={activeSessionId}
      />
    </div>
  );
}
