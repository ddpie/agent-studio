/**
 * Skill Assistant — AI sidebar for editing skill files via natural language.
 */
import { useState, useRef, useEffect, memo, useCallback } from "react";
import { useSkillAssistantStore, type SkillAssistantMessage } from "../../stores/skill-assistant-store";
import { Loader2, Send, Trash2, X, Square, RefreshCw, Pencil, Check } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { MODEL_GROUPS, findModelLabel } from "../../lib/models";

const AssistantMsg = memo(function AssistantMsg({ msg, isLastAssistant, isStreaming, onEdit }: {
  msg: SkillAssistantMessage;
  isLastAssistant: boolean;
  isStreaming: boolean;
  onEdit?: (id: string) => void;
}) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-2 group animate-[fadeSlideIn_0.2s_ease-out]`}>
      <div
        className={`relative max-w-[90%] rounded-xl px-3 py-2 text-xs leading-relaxed ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-100 dark:bg-gray-800 text-gray-800 dark:text-gray-200"
        }`}
      >
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
          <div className="prose prose-sm max-w-none dark:prose-invert [&_p]:my-1 [&_pre]:my-1 [&_pre]:text-[11px] [&_code]:text-[11px] [&_h1]:text-sm [&_h2]:text-[13px] [&_h3]:text-xs [&_li]:my-0.5 [&_ul]:my-1 [&_ol]:my-1">
            {msg.content.split(/(\n\n---file-updated:[^-]+---\n\n)/).map((part, i) => {
              const updatedMatch = part.match(/---file-updated:(.+)---/);
              if (updatedMatch) {
                return (
                  <div key={i} className="flex items-center gap-2 my-2 px-2 py-1.5 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg text-green-600 dark:text-green-400 text-[11px]">
                    <Check className="w-3 h-3" />
                    Updated: {updatedMatch[1]}
                  </div>
                );
              }
              if (!part.trim()) return null;
              return <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>{part}</ReactMarkdown>;
            })}
          </div>
        ) : (
          <span className="text-gray-400 flex items-center gap-1">
            <Loader2 className="w-3 h-3 animate-spin" /> Thinking...
          </span>
        )}
        {!isUser && isLastAssistant && isStreaming && msg.content && (
          <div className="flex items-center mt-2 pt-1.5 border-t border-gray-200 dark:border-gray-700 text-[11px] text-blue-500 animate-pulse">
            <Loader2 className="w-3 h-3 animate-spin mr-1" />
            Generating...
          </div>
        )}
      </div>
    </div>
  );
});

interface SkillAssistantProps {
  skillId: string;
  currentPath: string;
  currentContent: string;
  allFiles: string[];
  onFileUpdate: (path: string, newContent: string) => void;
  getFileContent: (path: string) => string | null;
}

export default function SkillAssistant({ skillId, currentPath, currentContent, allFiles, onFileUpdate, getFileContent }: SkillAssistantProps) {
  const {
    messages, isStreaming, loading, panelOpen, selectedModelId,
    closePanel, sendMessage, cancelStreaming, clearHistory, setModel, openPanel,
  } = useSkillAssistantStore();

  // Ensure panel loads history for this skill
  useEffect(() => {
    if (panelOpen) openPanel(skillId);
  }, [skillId]);

  const [input, setInput] = useState("");
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [editingMsgId, setEditingMsgId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [panelWidth, setPanelWidth] = useState(350);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const draggingRef = useRef(false);
  const userScrolledUp = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
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
    if (panelOpen) inputRef.current?.focus();
  }, [panelOpen]);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    const startX = e.clientX;
    const startWidth = panelWidth;
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return;
      const delta = startX - ev.clientX;
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

  const handleSend = () => {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput("");
    sendMessage(text, { path: currentPath, content: currentContent, allFiles, getFileContent }, onFileUpdate);
  };

  const startEdit = (msgId: string) => {
    const msg = messages.find((m) => m.id === msgId);
    if (msg) { setEditingMsgId(msgId); setEditText(msg.content); }
  };

  const submitEdit = () => {
    if (!editingMsgId || !editText.trim()) return;
    const newContent = editText.trim();
    setEditingMsgId(null);
    // Trim history to before this message and resend
    const msgIdx = messages.findIndex((m) => m.id === editingMsgId);
    if (msgIdx === -1) return;
    const store = useSkillAssistantStore.getState();
    const trimmed = messages.slice(0, msgIdx);
    useSkillAssistantStore.setState({ messages: trimmed });
    store.sendMessage(newContent, { path: currentPath, content: currentContent, allFiles, getFileContent }, onFileUpdate);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex flex-col h-full border-l border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 relative animate-[slideInRight_0.2s_ease-out]" style={{ width: panelWidth, minWidth: 250 }}>
      <div
        onMouseDown={onDragStart}
        className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-400/30 active:bg-blue-400/50 z-10 transition-colors"
      />
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wider">AI Assistant</span>
          <div className="relative">
            <button
              onClick={() => setShowModelPicker(!showModelPicker)}
              className="text-[9px] px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              {findModelLabel(selectedModelId || MODEL_GROUPS[0].models[0].id)}
            </button>
            {showModelPicker && (
              <div className="absolute top-full left-0 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 w-48 max-h-60 overflow-y-auto">
                {MODEL_GROUPS.map((group) => (
                  <div key={group.label}>
                    <div className="px-2 py-0.5 text-[8px] font-medium text-gray-400 uppercase bg-gray-50 dark:bg-gray-700">{group.label}</div>
                    {group.models.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => { setModel(m.id); setShowModelPicker(false); }}
                        className={`w-full text-left px-2 py-1 text-[10px] hover:bg-blue-50 dark:hover:bg-blue-900/30 ${selectedModelId === m.id ? "text-blue-600 bg-blue-50/50 dark:bg-blue-900/20" : "text-gray-700 dark:text-gray-300"}`}
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
            <button onClick={clearHistory} className="p-1 text-gray-400 hover:text-red-500 transition-colors" title="Clear history">
              <Trash2 className="w-3 h-3" />
            </button>
          )}
          <button onClick={closePanel} className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors" title="Close">
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
            <p className="text-xs text-gray-400 mb-2">Ask me to modify this skill file.</p>
            <div className="space-y-1">
              {[
                "Improve the skill description",
                "Add error handling to the script",
                "Translate to English",
              ].map((s) => (
                <button
                  key={s}
                  onClick={() => { setInput(s); inputRef.current?.focus(); }}
                  className="block w-full text-left text-[11px] text-gray-500 dark:text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 px-2 py-1 rounded transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, idx) => {
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
                        className="w-full px-2 py-1.5 rounded-lg border-2 border-blue-400 text-xs focus:outline-none resize-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                      />
                      <div className="flex justify-end gap-1 mt-0.5">
                        <button onClick={() => setEditingMsgId(null)} className="text-[10px] px-1.5 py-0.5 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded">Cancel</button>
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
            })}
            {messages.length > 0 && !isStreaming && messages[messages.length - 1]?.role === "assistant" && messages[messages.length - 1]?.content && (
              <div className="flex justify-start">
                <button
                  onClick={() => {
                    const lastUserIdx = messages.findLastIndex((m) => m.role === "user");
                    if (lastUserIdx === -1) return;
                    const content = messages[lastUserIdx].content;
                    const trimmed = messages.slice(0, lastUserIdx);
                    useSkillAssistantStore.setState({ messages: trimmed });
                    sendMessage(content, { path: currentPath, content: currentContent, allFiles, getFileContent }, onFileUpdate);
                  }}
                  className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-blue-600 transition-colors"
                >
                  <RefreshCw className="w-3 h-3" /> Regenerate
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-gray-100 dark:border-gray-700 px-3 py-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe what to change..."
            rows={1}
            className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg resize-none outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            style={{ maxHeight: 80 }}
          />
          {isStreaming ? (
            <button onClick={cancelStreaming} className="p-1.5 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors" title="Stop">
              <Square className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button onClick={handleSend} disabled={!input.trim()} className="p-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors">
              <Send className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
