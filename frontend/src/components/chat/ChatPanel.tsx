import { useState, useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useChatStore } from "../../stores/chat-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { Send, Loader2, Trash2, X, Plus, History, Clock, Square, RefreshCw, Download, Paperclip, FileText, Image as ImageIcon } from "lucide-react";
import "katex/dist/katex.min.css";
import { fetchAgentMetadataLight, type AgentMetadata } from "../../lib/agent-metadata";
import { useUISettings } from "../../stores/ui-settings-store";
import { uploadImageToS3, uploadFileToS3 } from "../../lib/s3-utils";
import { agentConfig } from "../../config";
import { MODEL_GROUPS, DEFAULT_MODEL_ID, findModelLabel } from "../../lib/models";
import ChatMessage from "./ChatMessage";

export default function ChatPanel() {
  const { t } = useTranslation();
  const { agentId } = useParams();
  const {
    messages, isStreaming, statusText, sendMessage, cancelStreaming,
    switchAgent, newSession, loadSession, deleteSession,
    getAgentSessions, activeSessionId, selectedModelId, setSelectedModel: storeSetModel,
    regenerateLastMessage, activeTool,
  } = useChatStore();
  const { agents, fetchAgents } = useAgentListStore();
  const agentName = agents.find(a => a.id === agentId)?.displayName || null;
  const [input, setInput] = useState("");
  const [pastedImages, setPastedImages] = useState<string[]>([]);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<Array<{ name: string; size: number; s3Key: string; uploading?: boolean }>>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectedModel = selectedModelId || DEFAULT_MODEL_ID;
  const setSelectedModel = (id: string) => storeSetModel(id);
  const [metadata, setMetadata] = useState<AgentMetadata | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const modelPickerRef = useRef<HTMLDivElement>(null);
  const prevStreamingRef = useRef(false);
  const savedInputRef = useRef("");
  const { inputHeight, setInputHeight } = useUISettings();

  const onInputDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = inputHeight;
    const onMove = (ev: MouseEvent) => {
      const newH = Math.min(Math.max(startH - (ev.clientY - startY), 44), 400);
      setInputHeight(newH);
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [inputHeight, setInputHeight]);

  const imagesAllowed = !agentId || metadata?.supports_images === true;
  const agentSessions = getAgentSessions();

  const sentMessages = messages
    .filter((m) => m.role === "user" && m.content)
    .map((m) => m.content);

  // Close dropdowns on outside click
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
  const userScrolledUp = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      userScrolledUp.current = !atBottom;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (!isStreaming) {
      userScrolledUp.current = false;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else if (!userScrolledUp.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
    }
  }, [messages, isStreaming]);

  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      fetchAgents();
      textareaRef.current?.focus();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, fetchAgents]);

  useEffect(() => {
    switchAgent(agentId || null, agentName);
  }, [agentId, agentName, switchAgent]);

  useEffect(() => {
    if (agentName && agentId) {
      fetchAgentMetadataLight(agentId).then((m) => {
        setMetadata(m);
        if (m?.default_model_id) {
          setSelectedModel(m.default_model_id);
        }
      });
    } else {
      setMetadata(null);
    }
  }, [agentId, agentName]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    if (!imagesAllowed) return;
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = async () => {
          const dataUrl = reader.result as string;
          setPastedImages((prev) => [...prev, dataUrl]);
          try {
            const s3Url = await uploadImageToS3(dataUrl);
            setUploadedImageUrls((prev) => [...prev, s3Url]);
          } catch (err) {
            console.error("Image upload failed:", err);
            setUploadedImageUrls((prev) => [...prev, dataUrl]);
          }
        };
        reader.readAsDataURL(file);
      }
    }
  }, [imagesAllowed]);

  const removeImage = (idx: number) => {
    setPastedImages((prev) => prev.filter((_, i) => i !== idx));
    setUploadedImageUrls((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const sessionId = activeSessionId || "tmp-" + Date.now();
    for (const file of files) {
      if (file.size > 5 * 1024 * 1024) {
        alert(t("chat.fileTooLarge", { name: file.name }));
        continue;
      }
      const placeholder = { name: file.name, size: file.size, s3Key: "", uploading: true };
      setAttachedFiles((prev) => [...prev, placeholder]);
      try {
        const { key } = await uploadFileToS3(file, sessionId);
        setAttachedFiles((prev) =>
          prev.map((f) => f === placeholder ? { ...f, s3Key: key, uploading: false } : f)
        );
      } catch (err) {
        console.error("File upload failed:", err);
        setAttachedFiles((prev) => prev.filter((f) => f !== placeholder));
        alert(t("chat.uploadFailed", { name: file.name }));
      }
    }
    e.target.value = "";
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    if (attachedFiles.some((f) => f.uploading)) return;

    let messageText = input.trim();
    const fileAttachments = attachedFiles.filter((f) => f.s3Key);
    if (fileAttachments.length > 0) {
      const bucket = agentConfig.s3Bucket;
      for (const f of fileAttachments) {
        messageText += `\n\n[Attached file: ${f.name} (${(f.size / 1024).toFixed(1)}KB) — use s3_read(bucket="${bucket}", key="${f.s3Key}") to read this file]`;
      }
    }

    const imageUrlsToSend = uploadedImageUrls.length > 0 ? uploadedImageUrls : (pastedImages.length > 0 ? pastedImages : undefined);
    sendMessage(messageText, imageUrlsToSend, selectedModel, fileAttachments.length > 0 ? fileAttachments : undefined);
    setInput("");
    setPastedImages([]);
    setUploadedImageUrls([]);
    setAttachedFiles([]);
    setHistoryIdx(-1);
    savedInputRef.current = "";
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
      return;
    }

    if (e.key === "ArrowUp" && sentMessages.length > 0) {
      const textarea = textareaRef.current;
      if (textarea && (textarea.selectionStart === 0 || !input)) {
        e.preventDefault();
        if (historyIdx === -1) {
          savedInputRef.current = input;
        }
        const newIdx = Math.min(historyIdx + 1, sentMessages.length - 1);
        setHistoryIdx(newIdx);
        setInput(sentMessages[sentMessages.length - 1 - newIdx]);
      }
    }

    if (e.key === "ArrowDown" && historyIdx >= 0) {
      e.preventDefault();
      const newIdx = historyIdx - 1;
      if (newIdx < 0) {
        setHistoryIdx(-1);
        setInput(savedInputRef.current);
      } else {
        setHistoryIdx(newIdx);
        setInput(sentMessages[sentMessages.length - 1 - newIdx]);
      }
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate">
            {agentId ? agentName : t("agents.metaAgent")}
          </h2>
          <p className="text-xs text-gray-500 truncate">
            {agentId
              ? t("chat.chattingWith", { name: agentName })
              : t("chat.metaSubtitle")}
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
                        onClick={() => { setSelectedModel(m.id); setShowModelPicker(false); }}
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
                  <span className="text-xs text-gray-400">{t("chat.sessionCount", { count: agentSessions.length })}</span>
                </div>
                {agentSessions.length === 0 ? (
                  <div className="p-4 text-center text-xs text-gray-400">{t("chat.noSessions")}</div>
                ) : (
                  agentSessions.map((session) => (
                    <div
                      key={session.id}
                      className={`flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer group ${
                        session.id === activeSessionId ? "bg-blue-50" : ""
                      }`}
                      onClick={() => { loadSession(session.id); setShowHistory(false); }}
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
                        onClick={(e) => { e.stopPropagation(); deleteSession(session.id); }}
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
          {/* Export conversation */}
          {messages.length > 0 && (
            <button
              onClick={() => {
                const agentNameExport = agentName || t("agents.metaAgent");
                const lines = [`# ${agentNameExport}\n`];
                for (const msg of messages) {
                  if (!msg.content) continue;
                  if (msg.role === "user") {
                    lines.push(`**User:**\n${msg.content}\n`);
                  } else if (msg.role === "assistant") {
                    lines.push(`**${agentNameExport}:**\n${msg.content}\n`);
                  }
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
          <button
            onClick={newSession}
            className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg hover:bg-blue-50"
            title={t("chat.newSession")}
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <p className="text-4xl mb-4">{agentId ? "💬" : "🤖"}</p>
            <p className="text-lg font-medium text-gray-600 dark:text-gray-400">
              {metadata?.welcome_message || (agentId ? t("chat.chatWith", { name: agentName }) : t("chat.welcomeMeta"))}
            </p>
            {!agentId && (
              <p className="text-sm mt-1">{t("chat.metaSubtitle")}</p>
            )}
            <div className="mt-6 grid gap-2 text-sm w-full max-w-lg">
              {(metadata?.suggestions || (!agentId ? [
                t("chat.defaultSuggestion1"),
                t("chat.defaultSuggestion2"),
                t("chat.defaultSuggestion3"),
              ] : [])).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => { setInput(suggestion); }}
                  className="text-left px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {(() => {
          const lastAssistantIdx = messages.findLastIndex((m) => m.role === "assistant");
          return messages.map((msg, idx) => (
            <ChatMessage
              key={msg.id}
              message={msg}
              isLastAssistant={idx === lastAssistantIdx}
              isStreaming={isStreaming}
            />
          ));
        })()}
        {messages.length > 0 && !isStreaming && messages[messages.length - 1]?.role === "assistant" && messages[messages.length - 1]?.content && (
          <div className="flex justify-start mb-4 -mt-2">
            <button
              onClick={regenerateLastMessage}
              className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 px-2 py-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
              title={t("chat.regenerate")}
            >
              <RefreshCw className="w-3 h-3" />
              {t("common.regenerate")}
            </button>
          </div>
        )}
        {statusText && (
          <div className="flex justify-start mb-4">
            <div className="rounded-2xl px-4 py-3 bg-amber-50 border border-amber-200 text-amber-800">
              <div className="flex items-center gap-2 text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>{statusText}</span>
              </div>
            </div>
          </div>
        )}
        {activeTool && !statusText && (
          <div className="flex justify-start mb-4">
            <div className="rounded-xl px-3 py-2 bg-blue-50 border border-blue-200 text-blue-700">
              <div className="flex items-center gap-2 text-xs">
                <Loader2 className="w-3 h-3 animate-spin" />
                <span dangerouslySetInnerHTML={{ __html: t("chat.calling", { tool: activeTool }) }} />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div>
        <div
          onMouseDown={onInputDragStart}
          onDoubleClick={() => setInputHeight(inputHeight > 60 ? 44 : 160)}
          className="h-1 cursor-row-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 border-t border-gray-200 dark:border-gray-700 transition-colors"
          title={t("chat.dragExpand")}
        />
        <div className="px-4 py-2">
        {pastedImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pastedImages.map((src, idx) => (
              <div key={idx} className="relative group">
                <img src={src} className="w-16 h-16 rounded-lg object-cover border border-gray-200 dark:border-gray-700" />
                <button
                  onClick={() => removeImage(idx)}
                  className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {attachedFiles.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {attachedFiles.map((f, idx) => (
              <div key={idx} className="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-100 dark:bg-gray-700 rounded-lg text-[11px] text-gray-600 dark:text-gray-400 border border-gray-200 dark:border-gray-700">
                {f.uploading ? <Loader2 className="w-3 h-3 animate-spin text-blue-500" /> : <FileText className="w-3 h-3 text-gray-400" />}
                <span className="max-w-32 truncate font-medium">{f.name}</span>
                <span className="text-gray-400">{(f.size / 1024).toFixed(1)}KB</span>
                {!f.uploading && (
                  <button
                    onClick={() => setAttachedFiles((prev) => prev.filter((_, i) => i !== idx))}
                    className="text-gray-300 hover:text-red-500 ml-0.5"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.tsv,.json,.txt,.md,.py,.yaml,.yml,.xml,.html,.log,.sql"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="p-2.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 flex-shrink-0"
            title={t("chat.attachFile")}
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => { setInput(e.target.value); setHistoryIdx(-1); }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={t("chat.placeholder")}
            rows={1}
            style={{ height: inputHeight }}
            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm resize-none overflow-auto"
            disabled={isStreaming}
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={cancelStreaming}
              className="p-2.5 rounded-xl bg-red-500 text-white hover:bg-red-600 flex-shrink-0"
              title={t("chat.stopGenerating")}
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="p-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </form>
        </div>
      </div>
    </div>
  );
}
