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
import { uploadImageToS3, uploadFileToS3, fetchSignedS3 } from "../../lib/s3-utils";
import { agentConfig } from "../../config";
import ImageLightbox from "../ui/ImageLightbox";

import { useAgentEditStore } from "../../stores/agent-edit-store";

function AgentProposalCard({ json }: { json: string }) {
  const { openNewWithData } = useAgentEditStore();
  let proposal: Record<string, unknown> | null = null;
  let parseError = "";
  try {
    proposal = JSON.parse(json);
  } catch (e) {
    // Try to repair common LLM JSON issues
    try {
      const repaired = json.replace(/,\s*}/g, "}").replace(/,\s*]/g, "]");
      proposal = JSON.parse(repaired);
    } catch {
      parseError = e instanceof Error ? e.message : "Invalid JSON";
    }
  }

  if (!proposal) {
    // Heuristic: if JSON doesn't end with }, it's still streaming (incomplete)
    const looksComplete = json.trimEnd().endsWith("}");
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 my-2 text-xs not-prose">
        {parseError && looksComplete ? (
          <div>
            <p className="text-red-500 text-[11px] mb-1">Failed to parse agent proposal</p>
            <details className="text-[10px] text-gray-500">
              <summary className="cursor-pointer">Show raw JSON</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all bg-gray-100 p-2 rounded max-h-40 overflow-y-auto">{json}</pre>
            </details>
          </div>
        ) : (
          <div className="animate-pulse">
            <div className="h-4 bg-gray-200 rounded w-1/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-2/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-1/2 mb-2" />
            <div className="text-[11px] text-gray-500 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> Generating proposal...
            </div>
          </div>
        )}
      </div>
    );
  }

  const name = String(proposal.agent_name || "");
  const desc = String(proposal.description || "");
  const template = String(proposal.template_id || "");
  const welcome = String(proposal.welcome_message || "");
  const suggestions = String(proposal.suggestions || "").split("|").filter(Boolean);
  const toolNames = String(proposal.tool_names || "").split(",").filter(Boolean);
  const tier = String(proposal.permission_tier || "readonly");
  const supportsImages = Boolean(proposal.supports_images);
  const systemPrompt = String(proposal.system_prompt || "");
  const toolDefs = String(proposal.tool_definitions || "");

  const handleEditAndCreate = () => {
    openNewWithData({
      name,
      display_name: name,
      description: desc,
      template_id: template,
      system_prompt: systemPrompt,
      tool_definitions: toolDefs,
      tool_names: toolNames.join(","),
      welcome_message: welcome,
      suggestions,
      supports_images: supportsImages,
    } as Partial<AgentMetadata>);
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
      {systemPrompt && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">System Prompt</summary>
          <pre className="mt-1 p-2 bg-white border border-gray-200 rounded text-[11px] text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">{systemPrompt}</pre>
        </details>
      )}
      {toolDefs && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">Tool Definitions</summary>
          <pre className="mt-1 p-2 bg-gray-900 text-green-300 rounded text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">{toolDefs}</pre>
        </details>
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

import { MODEL_GROUPS, DEFAULT_MODEL_ID, findModelLabel } from "../../lib/models";

function CopyButtons({ content }: { content: string }) {
  const [copied, setCopied] = useState<"text" | "md" | null>(null);

  // Strip tool-call <details> blocks, SVG/HTML rich output, and agent-proposal code blocks before copying
  const clean = (s: string) => s
    .replace(/<details class="tool-call">[\s\S]*?<\/details>/g, "")
    .replace(/<div class="tool-rich-output">[\s\S]*?<\/div>/g, "\n[Chart]\n")
    .replace(/```agent-proposal\n[\s\S]*?```/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  const copyAs = async (mode: "text" | "md") => {
    const cleaned = clean(content);
    const text = mode === "md" ? cleaned : cleaned.replace(/[#*`_~\[\]()>|\\-]/g, "").replace(/\n{3,}/g, "\n\n");
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
    // Strip [Attached file: ...] lines from edit textarea — file cards handle display
    const cleanContent = message.attachments?.length
      ? message.content.replace(/\n\n\[Attached file:[^\]]*\]/g, "").trim()
      : message.content;
    setEditText(cleanContent);
    setEditing(true);
  };

  const submitEdit = () => {
    if (editText.trim() && editText !== message.content) {
      // Re-append attachment references for the agent to use
      let finalText = editText.trim();
      if (message.attachments?.length) {
        const bucket = agentConfig.s3Bucket;
        for (const f of message.attachments) {
          finalText += `\n\n[Attached file: ${f.name} (${(f.size / 1024).toFixed(1)}KB) — use s3_read(bucket="${bucket}", key="${f.s3Key}") to read this file]`;
        }
      }
      editAndResend(message.id, finalText);
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
            : "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100"
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
        {/* Show attached files as cards with download */}
        {message.attachments && message.attachments.length > 0 && (
          <div className="flex gap-2 flex-wrap mb-2">
            {message.attachments.map((f, i) => (
              <button
                key={i}
                onClick={async () => {
                  try {
                    const s3Url = `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/${f.s3Key}`;
                    const blobUrl = await fetchSignedS3(s3Url);
                    const a = document.createElement("a");
                    a.href = blobUrl;
                    a.download = f.name;
                    a.click();
                    URL.revokeObjectURL(blobUrl);
                  } catch { /* ignore */ }
                }}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] border cursor-pointer transition-colors ${isUser ? "bg-white/10 border-white/20 text-white/90 hover:bg-white/20" : "bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100"}`}
                title="Click to download"
              >
                <FileText className="w-3.5 h-3.5 flex-shrink-0" />
                <span className="font-medium max-w-40 truncate">{f.name}</span>
                <span className={isUser ? "text-white/50" : "text-gray-400"}>{(f.size / 1024).toFixed(1)}KB</span>
                <Download className="w-3 h-3 flex-shrink-0 opacity-50" />
              </button>
            ))}
          </div>
        )}
        {message.content ? (
          <div className={`prose prose-sm max-w-none ${isUser ? "prose-invert" : ""}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]} rehypePlugins={[rehypeRaw, rehypeKatex]} components={mdComponents}>{
              // Strip [Attached file: ...] lines from display — file cards handle this
              message.attachments?.length
                ? message.content.replace(/\n\n\[Attached file:[^\]]*\]/g, "").trim()
                : message.content
            }</ReactMarkdown>
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

  const selectedModelLabel = findModelLabel(selectedModel);
  const userScrolledUp = useRef(false);

  // Track user scroll in chat area
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
      // Streaming ended — reset and scroll to bottom
      userScrolledUp.current = false;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else if (!userScrolledUp.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: "instant" });
    }
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
      fetchAgentMetadata(targetAgentId).then((m) => {
        setMetadata(m);
        // Auto-select agent's default model if set
        if (m?.default_model_id) {
          setSelectedModel(m.default_model_id);
        }
      });
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

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const sessionId = activeSessionId || "tmp-" + Date.now();
    for (const file of files) {
      if (file.size > 5 * 1024 * 1024) {
        alert(`File ${file.name} is too large (max 5MB)`);
        continue;
      }
      // Add placeholder with uploading state
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
        alert(`Upload failed for ${file.name}`);
      }
    }
    e.target.value = "";
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming) return;
    // Don't submit while files are still uploading
    if (attachedFiles.some((f) => f.uploading)) return;

    // Build message text with S3 file references (not raw content)
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
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate">
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
              <div key={idx} className="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-100 rounded-lg text-[11px] text-gray-600 border border-gray-200">
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
            className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm resize-none overflow-auto"
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
