import { useState, useRef, useEffect, useCallback, memo } from "react";
import { useChatStore, type Message, type ChatSession } from "../../stores/chat-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { Send, Loader2, Trash2, X, Plus, History, Clock, Square, Copy, FileText, Check, RefreshCw, Download, Pencil, Paperclip } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { fetchAgentMetadata, type AgentMetadata } from "../../lib/agent-metadata";
import { useUISettings } from "../../stores/ui-settings-store";
import { uploadImageToS3 } from "../../lib/image-upload";
import ImageLightbox from "../ui/ImageLightbox";

import { useAgentEditStore } from "../../stores/agent-edit-store";

function AgentProposalCard({ json }: { json: string }) {
  const { openEdit } = useAgentEditStore();
  let proposal: Record<string, unknown>;
  try {
    proposal = JSON.parse(json);
  } catch {
    return <pre className="text-xs text-red-500">Invalid proposal JSON</pre>;
  }

  const name = String(proposal.agent_name || "");
  const desc = String(proposal.description || "");
  const template = String(proposal.template_id || "");
  const welcome = String(proposal.welcome_message || "");
  const suggestions = String(proposal.suggestions || "").split("|").filter(Boolean);
  const toolNames = String(proposal.tool_names || "").split(",").filter(Boolean);
  const tier = String(proposal.permission_tier || "readonly");
  const supportsImages = Boolean(proposal.supports_images);

  const handleEditAndCreate = () => {
    openEdit("__new__", name);
    setTimeout(() => {
      const store = useAgentEditStore.getState();
      store.updateField("name", name);
      store.updateField("display_name", name);
      store.updateField("description", desc);
      store.updateField("template_id", template);
      store.updateField("system_prompt", String(proposal.system_prompt || ""));
      store.updateField("welcome_message", welcome);
      store.updateField("suggestions", suggestions);
      store.updateField("supports_images", supportsImages);
    }, 100);
  };

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/50 p-3 my-2 text-xs not-prose">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-blue-900">{name}</span>
        <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-600 rounded">{tier}</span>
      </div>
      {desc && <p className="text-gray-600 mb-2">{desc}</p>}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500 mb-2">
        {template && <div>Template: <span className="text-gray-700">{template}</span></div>}
        <div>Images: <span className="text-gray-700">{supportsImages ? "Yes" : "No"}</span></div>
        {toolNames.length > 0 && (
          <div className="col-span-2">Tools: {toolNames.map(t => (
            <span key={t} className="inline-block px-1.5 py-0.5 bg-white border border-gray-200 rounded text-[10px] mr-1">{t.trim()}</span>
          ))}</div>
        )}
      </div>
      {suggestions.length > 0 && (
        <div className="text-[10px] text-gray-400 mb-2">
          Suggestions: {suggestions.join(" / ")}
        </div>
      )}
      <button
        onClick={handleEditAndCreate}
        className="w-full mt-1 px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
      >
        Edit & Create
      </button>
    </div>
  );
}

const mdComponents: Components = {
  code({ className, children, ...props }) {
    const match = /language-([\w-]+)/.exec(className || "");
    const code = String(children).replace(/\n$/, "");
    if (match) {
      if (match[1] === "agent-proposal") {
        return <AgentProposalCard json={code} />;
      }
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

const ChatMessage = memo(function ChatMessage({ message, isLastAssistant, isStreaming }: { message: Message; isLastAssistant: boolean; isStreaming: boolean }) {
  const isUser = message.role === "user";
  const showTypingIndicator = isLastAssistant && isStreaming && message.role === "assistant";
  const showCopy = !isUser && message.content && !showTypingIndicator;
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const { editAndResend } = useChatStore();

  const startEdit = () => {
    setEditText(message.content);
    setEditing(true);
  };

  const submitEdit = () => {
    if (editText.trim() && editText !== message.content) {
      editAndResend(message.id, editText.trim());
    }
    setEditing(false);
  };

  if (editing) {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[80%] w-full">
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submitEdit(); }
              if (e.key === "Escape") setEditing(false);
            }}
            autoFocus
            rows={3}
            className="w-full px-4 py-3 rounded-2xl border-2 border-blue-400 text-sm focus:outline-none resize-none"
          />
          <div className="flex justify-end gap-1.5 mt-1">
            <button onClick={() => setEditing(false)} className="text-[11px] px-2 py-0.5 text-gray-500 hover:bg-gray-100 rounded">Cancel</button>
            <button onClick={submitEdit} className="text-[11px] px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-700">Send</button>
          </div>
        </div>
      </div>
    );
  }

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
        {/* Edit button for user messages */}
        {isUser && message.content && !isStreaming && (
          <button
            onClick={startEdit}
            className="absolute -bottom-1 -left-1 w-5 h-5 bg-white border border-gray-200 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
            title="Edit message"
          >
            <Pencil className="w-2.5 h-2.5 text-gray-400" />
          </button>
        )}
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
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]} rehypePlugins={[rehypeRaw, rehypeKatex]} components={mdComponents}>{message.content}</ReactMarkdown>
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
});

export default function ChatPanel() {
  const {
    messages, isStreaming, statusText, sendMessage, cancelStreaming, clearMessages,
    targetAgentId, targetAgentName, newSession, loadSession, deleteSession,
    getAgentSessions, activeSessionId, selectedModelId, setSelectedModel: storeSetModel,
    regenerateLastMessage, activeTool,
  } = useChatStore();
  const { fetchAgents } = useAgentListStore();
  const [input, setInput] = useState("");
  const [pastedImages, setPastedImages] = useState<string[]>([]); // base64 for preview
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]); // S3 URLs for sending
  const [attachedFiles, setAttachedFiles] = useState<Array<{ name: string; content: string }>>([]);
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

  // Meta-Agent always supports images; sub-agents depend on metadata
  const imagesAllowed = !targetAgentId || metadata?.supports_images === true;
  const agentSessions = getAgentSessions();

  // Sent messages for arrow-up/down history
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

  const selectedModelLabel = MODEL_GROUPS.flatMap((g) => g.models).find((m) => m.id === selectedModel)?.label || "Select";

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Instant scroll during streaming to avoid jitter, smooth otherwise
    el.scrollTo({ top: el.scrollHeight, behavior: isStreaming ? "instant" : "smooth" });
  }, [messages, isStreaming]);

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
          // Upload to S3 in background
          try {
            const s3Url = await uploadImageToS3(dataUrl);
            setUploadedImageUrls((prev) => [...prev, s3Url]);
          } catch (err) {
            console.error("Image upload failed:", err);
            // Fallback: keep base64 data URL
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

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of files) {
      if (file.size > 100 * 1024) {
        alert(`File ${file.name} is too large (max 100KB)`);
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => {
        setAttachedFiles((prev) => [...prev, { name: file.name, content: reader.result as string }]);
      };
      reader.readAsText(file);
    }
    e.target.value = "";
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;

    // Append file contents to the message
    let messageText = input.trim();
    if (attachedFiles.length > 0) {
      for (const f of attachedFiles) {
        messageText += `\n\n<file name="${f.name}">\n${f.content}\n</file>`;
      }
    }

    const imageUrlsToSend = uploadedImageUrls.length > 0 ? uploadedImageUrls : (pastedImages.length > 0 ? pastedImages : undefined);
    sendMessage(messageText, imageUrlsToSend, selectedModel);
    setInput("");
    setPastedImages([]);
    setUploadedImageUrls([]);
    setAttachedFiles([]);
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
          {/* Model selector (custom dropdown) */}
          <div className="relative" ref={modelPickerRef}>
            <button
              onClick={() => setShowModelPicker(!showModelPicker)}
              className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 flex items-center gap-1"
            >
              {selectedModelLabel}
              <svg className="w-3 h-3 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
            </button>
            {showModelPicker && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-white rounded-lg shadow-lg border border-gray-200 z-50 max-h-80 overflow-y-auto">
                {MODEL_GROUPS.map((group) => (
                  <div key={group.label}>
                    <div className="px-2 py-1 text-[9px] font-medium text-gray-400 uppercase tracking-wide bg-gray-50">{group.label}</div>
                    {group.models.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => { setSelectedModel(m.id); setShowModelPicker(false); }}
                        className={`w-full text-left px-3 py-1 text-[10px] hover:bg-blue-50 ${selectedModel === m.id ? "text-blue-600 bg-blue-50/50" : "text-gray-700"}`}
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
          {/* Export conversation */}
          {messages.length > 0 && (
            <button
              onClick={() => {
                const agentName = targetAgentName || "Meta Agent";
                const lines = [`# ${agentName}\n`];
                for (const msg of messages) {
                  if (!msg.content) continue;
                  if (msg.role === "user") {
                    lines.push(`**User:**\n${msg.content}\n`);
                  } else if (msg.role === "assistant") {
                    lines.push(`**${agentName}:**\n${msg.content}\n`);
                  }
                }
                const md = lines.join("\n");
                const blob = new Blob([md], { type: "text/markdown" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `${agentName}-${new Date().toISOString().slice(0, 10)}.md`;
                a.click();
                URL.revokeObjectURL(url);
              }}
              className="p-1.5 text-gray-400 hover:text-blue-600 rounded-lg hover:bg-blue-50"
              title="Export as Markdown"
            >
              <Download className="w-4 h-4" />
            </button>
          )}
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
        {/* Regenerate button after last assistant message */}
        {messages.length > 0 && !isStreaming && messages[messages.length - 1]?.role === "assistant" && messages[messages.length - 1]?.content && (
          <div className="flex justify-start mb-4 -mt-2">
            <button
              onClick={regenerateLastMessage}
              className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 px-2 py-1 rounded-md hover:bg-gray-100 transition-colors"
              title="Regenerate response"
            >
              <RefreshCw className="w-3 h-3" />
              Regenerate
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
                <span>Calling <strong>{activeTool}</strong></span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div>
        {/* Drag handle to resize input area upward */}
        <div
          onMouseDown={onInputDragStart}
          onDoubleClick={() => setInputHeight(inputHeight > 60 ? 44 : 160)}
          className="h-1 cursor-row-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 border-t border-gray-200 transition-colors"
          title="Drag up to expand, double-click to toggle"
        />
        <div className="px-4 py-2">
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
        {/* Attached file previews */}
        {attachedFiles.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {attachedFiles.map((f, idx) => (
              <div key={idx} className="flex items-center gap-1 px-2 py-1 bg-gray-100 rounded-lg text-[11px] text-gray-600 group">
                <Paperclip className="w-3 h-3" />
                <span className="max-w-32 truncate">{f.name}</span>
                <button
                  onClick={() => setAttachedFiles((prev) => prev.filter((_, i) => i !== idx))}
                  className="text-gray-300 hover:text-red-500"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.json,.txt,.md,.py,.yaml,.yml,.xml,.html,.log,.sql"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="p-2.5 text-gray-400 hover:text-gray-600 flex-shrink-0"
            title="Attach file"
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => { setInput(e.target.value); setHistoryIdx(-1); }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={imagesAllowed ? "Type a message... (Shift+Enter for new line, paste images)" : "Type a message... (Shift+Enter for new line)"}
            rows={1}
            style={{ height: inputHeight }}
            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm resize-none overflow-auto"
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
    </div>
  );
}
