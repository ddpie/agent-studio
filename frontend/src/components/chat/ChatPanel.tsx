import { useState, useRef, useEffect, useCallback } from "react";
import { useChatStore, type Message, type ChatSession } from "../../stores/chat-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { Send, Loader2, Trash2, X, Plus, History, Clock, Square, Copy, FileText, Check } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { fetchAgentMetadata, type AgentMetadata } from "../../lib/agent-metadata";
import ImageLightbox from "../ui/ImageLightbox";

const mdComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const code = String(children).replace(/\n$/, "");
    if (match) {
      return <CodeBlock language={match[1]} code={code} />;
    }
    return <code className={className} {...props}>{children}</code>;
  },
};

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="relative group/code">
      <div className="absolute top-1 right-1 flex items-center gap-1 opacity-0 group-hover/code:opacity-100 transition-opacity z-10">
        <span className="text-[10px] text-gray-400 bg-white/80 px-1 rounded">{language}</span>
        <button onClick={handleCopy} className="p-1 bg-white/80 hover:bg-white rounded border border-gray-200" title="Copy code">
          {copied ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3 text-gray-400" />}
        </button>
      </div>
      <SyntaxHighlighter style={oneLight} language={language} PreTag="div" customStyle={{ margin: 0, borderRadius: "0.375rem", fontSize: "0.8em" }}>
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

const MODEL_GROUPS = [
  {
    label: "Claude 4.6",
    models: [
      { id: "us.anthropic.claude-opus-4-6-v1", label: "Opus 4.6 (US)" },
      { id: "global.anthropic.claude-opus-4-6-v1", label: "Opus 4.6 (Global)" },
      { id: "us.anthropic.claude-sonnet-4-6", label: "Sonnet 4.6 (US)" },
      { id: "global.anthropic.claude-sonnet-4-6", label: "Sonnet 4.6 (Global)" },
    ],
  },
  {
    label: "Claude 4.5",
    models: [
      { id: "us.anthropic.claude-opus-4-5-20251101-v1:0", label: "Opus 4.5 (US)" },
      { id: "global.anthropic.claude-opus-4-5-20251101-v1:0", label: "Opus 4.5 (Global)" },
      { id: "us.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5 (US)" },
      { id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Sonnet 4.5 (Global)" },
      { id: "us.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5 (US)" },
      { id: "global.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Haiku 4.5 (Global)" },
    ],
  },
  {
    label: "Claude 4",
    models: [
      { id: "us.anthropic.claude-opus-4-1-20250805-v1:0", label: "Opus 4.1 (US)" },
      { id: "us.anthropic.claude-opus-4-20250514-v1:0", label: "Opus 4 (US)" },
      { id: "us.anthropic.claude-sonnet-4-20250514-v1:0", label: "Sonnet 4 (US)" },
      { id: "global.anthropic.claude-sonnet-4-20250514-v1:0", label: "Sonnet 4 (Global)" },
    ],
  },
  {
    label: "Claude 3.x",
    models: [
      { id: "us.anthropic.claude-3-7-sonnet-20250219-v1:0", label: "3.7 Sonnet (US)" },
      { id: "us.anthropic.claude-3-5-sonnet-20241022-v2:0", label: "3.5 Sonnet v2 (US)" },
      { id: "us.anthropic.claude-3-5-haiku-20241022-v1:0", label: "3.5 Haiku (US)" },
    ],
  },
  {
    label: "Meta Llama",
    models: [
      { id: "us.meta.llama4-maverick-17b-instruct-v1:0", label: "Llama 4 Maverick 17B" },
      { id: "us.meta.llama4-scout-17b-instruct-v1:0", label: "Llama 4 Scout 17B" },
      { id: "us.meta.llama3-3-70b-instruct-v1:0", label: "Llama 3.3 70B" },
      { id: "us.meta.llama3-2-90b-instruct-v1:0", label: "Llama 3.2 90B" },
    ],
  },
  {
    label: "Other",
    models: [
      { id: "us.deepseek.r1-v1:0", label: "DeepSeek R1" },
      { id: "us.mistral.pixtral-large-2502-v1:0", label: "Mistral Pixtral Large" },
    ],
  },
];

const DEFAULT_MODEL_ID = MODEL_GROUPS[0].models[0].id;

function CopyButtons({ content }: { content: string }) {
  const [copied, setCopied] = useState<"text" | "md" | null>(null);

  const copyAs = async (mode: "text" | "md") => {
    const text = mode === "md" ? content : content.replace(/[#*`_~\[\]()>|\\-]/g, "").replace(/\n{3,}/g, "\n\n");
    await navigator.clipboard.writeText(text);
    setCopied(mode);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="absolute -top-1 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-white rounded-md shadow-sm border border-gray-200 p-0.5">
      <button onClick={() => copyAs("text")} className="p-1 hover:bg-gray-100 rounded" title="Copy as text">
        {copied === "text" ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3 text-gray-400" />}
      </button>
      <button onClick={() => copyAs("md")} className="p-1 hover:bg-gray-100 rounded" title="Copy as Markdown">
        {copied === "md" ? <Check className="w-3 h-3 text-green-500" /> : <FileText className="w-3 h-3 text-gray-400" />}
      </button>
    </div>
  );
}

function ChatMessage({ message, isLastAssistant, isStreaming }: { message: Message; isLastAssistant: boolean; isStreaming: boolean }) {
  const isUser = message.role === "user";
  const showTypingIndicator = isLastAssistant && isStreaming && message.role === "assistant";
  const showCopy = !isUser && message.content && !showTypingIndicator;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      <div
        className={`relative group max-w-[80%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-blue-600 text-white"
            : "bg-gray-100 text-gray-900"
        }`}
      >
        {showCopy && <CopyButtons content={message.content} />}
        {/* Show attached images */}
        {message.images && message.images.length > 0 && (
          <div className="flex gap-2 flex-wrap mb-2">
            {message.images.map((src, i) => (
              <ImageLightbox key={i} src={src} />
            ))}
          </div>
        )}
        {message.content ? (
          <div className={`prose prose-sm max-w-none ${isUser ? "prose-invert" : ""}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>{message.content}</ReactMarkdown>
            {showTypingIndicator && (
              <span className="inline-flex items-center gap-1 text-gray-400 text-xs mt-2">
                <Loader2 className="w-3 h-3 animate-spin" /> Working...
              </span>
            )}
          </div>
        ) : (
          <span className="inline-flex items-center gap-1 text-gray-400 text-sm">
            <Loader2 className="w-3 h-3 animate-spin" /> Thinking...
          </span>
        )}
      </div>
    </div>
  );
}

export default function ChatPanel() {
  const {
    messages, isStreaming, statusText, sendMessage, cancelStreaming, clearMessages,
    targetAgentId, targetAgentName, newSession, loadSession, deleteSession,
    getAgentSessions, activeSessionId,
  } = useChatStore();
  const { fetchAgents } = useAgentListStore();
  const [input, setInput] = useState("");
  const [pastedImages, setPastedImages] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL_ID);
  const [metadata, setMetadata] = useState<AgentMetadata | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const prevStreamingRef = useRef(false);
  const savedInputRef = useRef("");

  // Meta-Agent always supports images; sub-agents depend on metadata
  const imagesAllowed = !targetAgentId || metadata?.supports_images === true;
  const agentSessions = getAgentSessions();

  // Sent messages for arrow-up/down history
  const sentMessages = messages
    .filter((m) => m.role === "user" && m.content)
    .map((m) => m.content);

  // Close history dropdown on outside click
  useEffect(() => {
    if (!showHistory) return;
    const handler = (e: MouseEvent) => {
      if (historyRef.current && !historyRef.current.contains(e.target as Node)) {
        setShowHistory(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showHistory]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Refresh agent list when streaming finishes
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      fetchAgents();
      textareaRef.current?.focus();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming, fetchAgents]);

  // Load agent metadata when target changes
  useEffect(() => {
    if (targetAgentName && targetAgentId) {
      fetchAgentMetadata(targetAgentId).then(setMetadata);
    } else {
      setMetadata(null);
    }
  }, [targetAgentId, targetAgentName]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + "px";
    }
  }, [input]);

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
        reader.onload = () => {
          setPastedImages((prev) => [...prev, reader.result as string]);
        };
        reader.readAsDataURL(file);
      }
    }
  }, [imagesAllowed]);

  const removeImage = (idx: number) => {
    setPastedImages((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim(), pastedImages.length > 0 ? pastedImages : undefined, selectedModel);
    setInput("");
    setPastedImages([]);
    setHistoryIdx(-1);
    savedInputRef.current = "";
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // IME composition: don't intercept Enter during IME candidate selection
    if (e.nativeEvent.isComposing) return;

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
      return;
    }

    // Arrow up/down for sent message history
    if (e.key === "ArrowUp" && sentMessages.length > 0) {
      const textarea = textareaRef.current;
      // Only activate when cursor is at the start or input is empty
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
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gray-900 truncate">
            {targetAgentId ? targetAgentName : "Meta Agent"}
          </h2>
          <p className="text-xs text-gray-500 truncate">
            {targetAgentId
              ? `Chatting with ${targetAgentName}`
              : "Create & manage agents through conversation"}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Model selector */}
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            className="text-xs px-2 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-600"
          >
            {MODEL_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.models.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          {/* Session history */}
          <div className="relative" ref={historyRef}>
            <button
              onClick={() => setShowHistory(!showHistory)}
              className={`p-1.5 rounded-lg ${showHistory ? "text-blue-600 bg-blue-50" : "text-gray-400 hover:text-blue-600 hover:bg-blue-50"}`}
              title="Session history"
            >
              <History className="w-4 h-4" />
            </button>
            {showHistory && (
              <div className="absolute right-0 top-full mt-1 w-72 bg-white rounded-lg shadow-lg border border-gray-200 z-50 max-h-80 overflow-y-auto">
                <div className="p-2 border-b border-gray-100 flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-500">History</span>
                  <span className="text-xs text-gray-400">{agentSessions.length} sessions</span>
                </div>
                {agentSessions.length === 0 ? (
                  <div className="p-4 text-center text-xs text-gray-400">No past sessions</div>
                ) : (
                  agentSessions.map((session) => (
                    <div
                      key={session.id}
                      className={`flex items-center gap-2 px-3 py-2 hover:bg-gray-50 cursor-pointer group ${
                        session.id === activeSessionId ? "bg-blue-50" : ""
                      }`}
                      onClick={() => { loadSession(session.id); setShowHistory(false); }}
                    >
                      <Clock className="w-3 h-3 text-gray-300 flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-gray-700 truncate">{session.title}</p>
                        <p className="text-xs text-gray-400">
                          {new Date(session.updatedAt).toLocaleDateString()} {new Date(session.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          {" · "}{session.messages.length} msgs
                        </p>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteSession(session.id); }}
                        className="p-1 text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                        title="Delete session"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
          {/* New session */}
          <button
            onClick={newSession}
            className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg hover:bg-blue-50"
            title="New session"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <p className="text-4xl mb-4">{targetAgentId ? "💬" : "🤖"}</p>
            <p className="text-lg font-medium text-gray-600">
              {metadata?.welcome_message || (targetAgentId ? `Chat with ${targetAgentName}` : "Welcome to Meta Agent")}
            </p>
            {!targetAgentId && (
              <p className="text-sm mt-1">Create & manage agents through conversation</p>
            )}
            <div className="mt-6 grid gap-2 text-sm w-full max-w-lg">
              {(metadata?.suggestions || (!targetAgentId ? [
                "Create an agent that can search the web and summarize results",
                "Build a math tutor that can solve equations step by step",
                "Make a coding assistant that writes Python functions",
              ] : [])).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => { setInput(suggestion); }}
                  className="text-left px-4 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 text-gray-600"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, idx) => {
          const lastAssistantIdx = messages.findLastIndex((m) => m.role === "assistant");
          return (
            <ChatMessage
              key={msg.id}
              message={msg}
              isLastAssistant={idx === lastAssistantIdx}
              isStreaming={isStreaming}
            />
          );
        })}
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
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-gray-200">
        {/* Pasted image previews */}
        {pastedImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pastedImages.map((src, idx) => (
              <div key={idx} className="relative group">
                <img src={src} className="w-16 h-16 rounded-lg object-cover border border-gray-200" />
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
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => { setInput(e.target.value); setHistoryIdx(-1); }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={imagesAllowed ? "Type a message... (Shift+Enter for new line, paste images)" : "Type a message... (Shift+Enter for new line)"}
            rows={1}
            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm resize-none overflow-hidden"
            disabled={isStreaming}
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={cancelStreaming}
              className="p-2.5 rounded-xl bg-red-500 text-white hover:bg-red-600 flex-shrink-0"
              title="Stop generating"
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
  );
}
