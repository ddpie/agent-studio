/**
 * Edit Assistant — AI sidebar for editing agent config via natural language.
 */
import { useState, useRef, useEffect, memo, useCallback } from "react";
import { useEditAssistantStore, type AssistantMessage } from "../../stores/edit-assistant-store";
import { useAgentEditStore } from "../../stores/agent-edit-store";
import { Loader2, Send, Trash2, X, Square, RefreshCw, Pencil } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MODEL_GROUPS, findModelLabel } from "../../lib/models";

const AssistantMsg = memo(function AssistantMsg({ msg, isLastAssistant, isStreaming, onEdit }: {
  msg: AssistantMessage;
  isLastAssistant: boolean;
  isStreaming: boolean;
  onEdit?: (id: string) => void;
}) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-2 group`}>
      <div
        className={`relative max-w-[90%] rounded-xl px-3 py-2 text-xs leading-relaxed ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-100 text-gray-800"
        }`}
      >
        {/* Edit button for user messages */}
        {isUser && !isStreaming && onEdit && (
          <button
            onClick={() => onEdit(msg.id)}
            className="absolute -bottom-1 -left-1 w-4 h-4 bg-white border border-gray-200 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
            title="Edit"
          >
            <Pencil className="w-2 h-2 text-gray-400" />
          </button>
        )}
        {isUser ? (
          <span>{msg.content}</span>
        ) : msg.content ? (
          <div className="prose prose-sm max-w-none [&_p]:my-1 [&_pre]:my-1 [&_pre]:text-[11px] [&_code]:text-[11px] [&_h1]:text-sm [&_h2]:text-[13px] [&_h3]:text-xs [&_li]:my-0.5 [&_ul]:my-1 [&_ol]:my-1">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
          </div>
        ) : (
          <span className="text-gray-400 flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" /> Thinking...
          </span>
        )}
      </div>
    </div>
  );
});

export default function EditAssistant() {
  const {
    messages, isStreaming, loading, panelOpen, selectedModelId,
    closePanel, sendMessage, cancelStreaming, clearHistory, setModel,
    regenerateLastMessage, editAndResend,
  } = useEditAssistantStore();
  const { formData, updateField, editingAgentId } = useAgentEditStore();

  const [input, setInput] = useState("");
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [panelWidth, setPanelWidth] = useState(350);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const draggingRef = useRef(false);

  // Auto-scroll
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: isStreaming ? "instant" : "smooth" });
  }, [messages, isStreaming]);

  // Focus input when panel opens
  useEffect(() => {
    if (panelOpen) inputRef.current?.focus();
  }, [panelOpen]);

  // Drag to resize panel
  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    const startX = e.clientX;
    const startWidth = panelWidth;

    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return;
      const delta = startX - ev.clientX; // dragging left = wider
      setPanelWidth(Math.max(250, Math.min(600, startWidth + delta)));
    };
    const onUp = () => {
      draggingRef.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [panelWidth]);

  if (!panelOpen) return null;

  const onUpdateHandler = (updates: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(updates)) {
      updateField(key as keyof typeof formData, value as never);
    }
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput("");
    sendMessage(text, formData ? { ...formData } : {}, onUpdateHandler);
  };

  const handleRegenerate = () => {
    if (isStreaming) return;
    regenerateLastMessage(formData ? { ...formData } : {}, onUpdateHandler);
  };

  const startEdit = (msgId: string) => {
    const msg = messages.find((m) => m.id === msgId);
    if (msg) { setEditingMsgId(msgId); setEditText(msg.content); }
  };

  const submitEdit = () => {
    if (!editingMsgId || !editText.trim()) return;
    setEditingMsgId(null);
    editAndResend(editingMsgId, editText.trim(), formData ? { ...formData } : {}, onUpdateHandler);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full border-l border-gray-200 bg-white relative" style={{ width: panelWidth, minWidth: 250 }}>
      {/* Drag handle on left edge */}
      <div
        onMouseDown={onDragStart}
        className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/30 active:bg-blue-400/50 z-10 transition-colors"
      />
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50/50">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">AI Assistant</span>
          {/* Model picker */}
          <div className="relative">
            <button
              onClick={() => setShowModelPicker(!showModelPicker)}
              className="text-[9px] px-1.5 py-0.5 rounded border border-gray-200 bg-white text-gray-500 hover:bg-gray-50"
            >
              {findModelLabel(selectedModelId || MODEL_GROUPS[0].models[0].id)}
            </button>
            {showModelPicker && (
              <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 w-48 max-h-60 overflow-y-auto">
                {MODEL_GROUPS.map((group) => (
                  <div key={group.label}>
                    <div className="px-2 py-0.5 text-[8px] font-medium text-gray-400 uppercase bg-gray-50">{group.label}</div>
                    {group.models.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => { setModel(m.id); setShowModelPicker(false); }}
                        className={`w-full text-left px-2 py-1 text-[10px] hover:bg-blue-50 ${selectedModelId === m.id ? "text-blue-600 bg-blue-50/50" : "text-gray-700"}`}
                      >
                        {m.label}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {messages.length > 0 && (
            <button
              onClick={clearHistory}
              className="p-1 text-gray-400 hover:text-red-500 transition-colors"
              title="Clear history"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          )}
          <button
            onClick={closePanel}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
            title="Close"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-3">
        {loading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-gray-300" />
          </div>
        ) : messages.length === 0 ? (
          <div className="text-center py-8">
            <p className="text-xs text-gray-400 mb-2">Ask me to modify this agent's config.</p>
            <div className="space-y-1">
              {[
                "Translate the system prompt to English",
                "Add a region parameter to all tools",
                "Make the description more concise",
              ].map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); inputRef.current?.focus(); }}
                  className="block w-full text-left text-[11px] text-gray-500 hover:text-blue-600 hover:bg-blue-50 px-2 py-1 rounded transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, idx) => {
            const isLastAssistant = msg.role === "assistant" && idx === messages.length - 1;
            if (editingMsgId === msg.id) {
              return (
                <div key={msg.id} className="flex justify-end mb-2">
                  <div className="max-w-[90%] w-full">
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submitEdit(); }
                        if (e.key === "Escape") setEditingMsgId(null);
                      }}
                      autoFocus
                      rows={2}
                      className="w-full px-2 py-1.5 rounded-lg border-2 border-blue-400 text-xs focus:outline-none resize-none"
                    />
                    <div className="flex justify-end gap-1 mt-0.5">
                      <button onClick={() => setEditingMsgId(null)} className="text-[10px] px-1.5 py-0.5 text-gray-500 hover:bg-gray-100 rounded">Cancel</button>
                      <button onClick={submitEdit} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-700">Send</button>
                    </div>
                  </div>
                </div>
              );
            }
            return (
              <AssistantMsg
                key={msg.id}
                msg={msg}
                isLastAssistant={isLastAssistant}
                isStreaming={isStreaming}
                onEdit={msg.role === "user" ? startEdit : undefined}
              />
            );
          })
        )}
        {/* Regenerate button */}
        {messages.length > 0 && !isStreaming && messages[messages.length - 1]?.role === "assistant" && messages[messages.length - 1]?.content && (
          <div className="flex justify-start">
            <button
              onClick={handleRegenerate}
              className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-blue-600 transition-colors"
            >
              <RefreshCw className="w-3 h-3" /> Regenerate
            </button>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-100 px-3 py-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe what to change..."
            rows={1}
            className="flex-1 px-2 py-1.5 text-xs border border-gray-200 rounded-lg resize-none outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500"
            style={{ maxHeight: 80 }}
          />
          {isStreaming ? (
            <button
              onClick={cancelStreaming}
              className="p-1.5 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors"
              title="Stop"
            >
              <Square className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="p-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
