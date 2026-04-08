import { useState, useRef, useEffect, useCallback, memo } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useNavigate } from "react-router";
import { useChatStore, type Message } from "../../stores/chat-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { generateDownloadUrl } from "../../lib/s3-storage";
import { Send, Loader2, Trash2, X, Plus, History, Clock, Square, Copy, FileText, Check, RefreshCw, Download, Pencil, Paperclip, Image as ImageIcon } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { fetchAgentMetadataLight, type AgentMetadata } from "../../lib/agent-metadata";
import { useUISettings } from "../../stores/ui-settings-store";
import { uploadImageToS3, uploadFileToS3, fetchSignedS3 } from "../../lib/s3-utils";
import { agentConfig } from "../../config";
import ImageLightbox from "../ui/ImageLightbox";

import { useAgentEditStore } from "../../stores/agent-edit-store";

function AgentProposalCard({ json }: { json: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
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
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 p-3 my-2 text-xs not-prose">
        {parseError && looksComplete ? (
          <div>
            <p className="text-red-500 text-[11px] mb-1">{t("chat.parseFailed")}</p>
            <details className="text-[10px] text-gray-500">
              <summary className="cursor-pointer">{t("chat.showRawJson")}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all bg-gray-100 dark:bg-gray-700 p-2 rounded max-h-40 overflow-y-auto">{json}</pre>
            </details>
          </div>
        ) : (
          <div className="animate-pulse">
            <div className="h-4 bg-gray-200 rounded w-1/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-2/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-1/2 mb-2" />
            <div className="text-[11px] text-gray-500 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> {t("chat.generatingProposal")}
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
    // openNewWithData sets agentId synchronously — read it and navigate
    const draftId = useAgentEditStore.getState().agentId;
    if (draftId) navigate(`/agents/edit/${draftId}`);
  };

  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/20 p-3 my-2 text-xs not-prose">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-blue-900 dark:text-blue-100">{name}</span>
        <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 dark:bg-blue-800 text-blue-600 dark:text-blue-300 rounded">{tier}</span>
      </div>
      {desc && <p className="text-gray-600 dark:text-gray-400 mb-2">{desc}</p>}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500 mb-2">
        {template && <div>{t("chat.proposalTemplate")} <span className="text-gray-700 dark:text-gray-300">{template}</span></div>}
        <div>{t("chat.proposalImages")} <span className="text-gray-700 dark:text-gray-300">{supportsImages ? t("chat.yes") : t("chat.no")}</span></div>
        {toolNames.length > 0 && (
          <div className="col-span-2">{t("chat.proposalTools")} {toolNames.map(t2 => (
            <span key={t2} className="inline-block px-1.5 py-0.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[10px] mr-1">{t2.trim()}</span>
          ))}</div>
        )}
      </div>
      {suggestions.length > 0 && (
        <div className="text-[10px] text-gray-400 mb-2">
          {t("chat.proposalSuggestions")} {suggestions.join(" / ")}
        </div>
      )}
      {systemPrompt && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">{t("chat.proposalSystemPrompt")}</summary>
          <pre className="mt-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[11px] text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-48 overflow-y-auto">{systemPrompt}</pre>
        </details>
      )}
      {toolDefs && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">{t("chat.proposalToolDefs")}</summary>
          <pre className="mt-1 p-2 bg-gray-900 dark:bg-gray-950 text-green-300 rounded text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">{toolDefs}</pre>
        </details>
      )}
      <button
        onClick={handleEditAndCreate}
        className="w-full mt-1 px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
      >
        {t("chat.editAndCreate")}
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
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="relative group/code">
      <div className="absolute top-1 right-1 flex items-center gap-1 opacity-0 group-hover/code:opacity-100 transition-opacity z-10">
        <span className="text-[10px] text-gray-400 bg-white/80 dark:bg-gray-800/80 px-1 rounded">{language}</span>
        <button onClick={handleCopy} className="p-1 bg-white/80 dark:bg-gray-800/80 hover:bg-white dark:hover:bg-gray-800 rounded border border-gray-200 dark:border-gray-700" title={t("chat.copyCode")}>
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

function CopyButtons({ content, contentRef }: { content: string; contentRef?: React.RefObject<HTMLDivElement | null> }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<"text" | "md" | "rich" | null>(null);

  const stripNonContent = (s: string) => s
    .replace(/<details class="tool-call">[\s\S]*?<\/details>/g, "")
    .replace(/```agent-proposal\n[\s\S]*?```/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  // SVG → PNG data URL via Canvas
  const svgToPng = (svgEl: SVGSVGElement): Promise<string> =>
    new Promise((resolve) => {
      const svgStr = new XMLSerializer().serializeToString(svgEl);
      // Parse viewBox to get actual dimensions
      const vb = svgEl.getAttribute("viewBox")?.split(/[\s,]+/).map(Number);
      const svgW = vb && vb.length >= 4 ? vb[2] : svgEl.clientWidth || 800;
      const svgH = vb && vb.length >= 4 ? vb[3] : svgEl.clientHeight || 400;
      const scale = 2;
      const blob = new Blob([svgStr], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const img = new window.Image();
      img.onload = () => {
        const c = document.createElement("canvas");
        c.width = svgW * scale;
        c.height = svgH * scale;
        const ctx = c.getContext("2d")!;
        ctx.scale(scale, scale);
        ctx.drawImage(img, 0, 0, svgW, svgH);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/png"));
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(""); };
      img.src = url;
    });

  const copyAs = async (mode: "text" | "md" | "rich") => {
    if (mode === "text") {
      const text = stripNonContent(content)
        .replace(/<div class="tool-rich-output">[\s\S]*?<\/div>/g, "\n[Chart]\n")
        .replace(/[#*`_~\[\]()>|\\-]/g, "")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      await navigator.clipboard.writeText(text);
    } else if (mode === "md") {
      const md = stripNonContent(content)
        .replace(/<div class="tool-rich-output">[\s\S]*?<\/div>/g, "\n\n[Chart]\n\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim();
      await navigator.clipboard.writeText(md);
    } else {
      // Rich: grab rendered HTML from DOM, convert SVGs to PNG <img>
      if (!contentRef?.current) {
        await navigator.clipboard.writeText(content);
        return;
      }
      const clone = contentRef.current.cloneNode(true) as HTMLDivElement;
      // Remove tool-call details blocks from clone
      clone.querySelectorAll("details.tool-call").forEach(el => el.remove());
      // Convert SVGs to PNG images
      const svgs = clone.querySelectorAll("svg");
      for (const svg of svgs) {
        const png = await svgToPng(svg as SVGSVGElement);
        if (png) {
          const img = document.createElement("img");
          img.src = png;
          img.style.maxWidth = "100%";
          svg.parentElement?.replaceChild(img, svg);
        }
      }
      const html = clone.innerHTML;
      const plainText = clone.textContent || "";
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": new Blob([plainText], { type: "text/plain" }),
            "text/html": new Blob([html], { type: "text/html" }),
          }),
        ]);
      } catch {
        await navigator.clipboard.writeText(plainText);
      }
    }
    setCopied(mode);
    setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="absolute -top-1 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity bg-white dark:bg-gray-800 rounded-md shadow-sm border border-gray-200 dark:border-gray-700 p-0.5">
      <button onClick={() => copyAs("text")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyPlain")}>
        {copied === "text" ? <Check className="w-3 h-3 text-green-500" /> : <Copy className="w-3 h-3 text-gray-400" />}
      </button>
      <button onClick={() => copyAs("md")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyMarkdown")}>
        {copied === "md" ? <Check className="w-3 h-3 text-green-500" /> : <FileText className="w-3 h-3 text-gray-400" />}
      </button>
      <button onClick={() => copyAs("rich")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title={t("chat.copyRich")}>
        {copied === "rich" ? <Check className="w-3 h-3 text-green-500" /> : <ImageIcon className="w-3 h-3 text-gray-400" />}
      </button>
    </div>
  );
}

const ChatMessage = memo(function ChatMessage({ message, isLastAssistant, isStreaming }: { message: Message; isLastAssistant: boolean; isStreaming: boolean }) {
  const { t } = useTranslation();
  const isUser = message.role === "user";
  const showTypingIndicator = isLastAssistant && isStreaming && message.role === "assistant";
  const showCopy = !isUser && message.content && !showTypingIndicator;
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const { editAndResend } = useChatStore();
  const contentDivRef = useRef<HTMLDivElement>(null);

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
            <button onClick={() => setEditing(false)} className="text-[11px] px-2 py-0.5 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 rounded">{t("common.cancel")}</button>
            <button onClick={submitEdit} className="text-[11px] px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-700">{t("common.send")}</button>
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
        {showCopy && <CopyButtons content={message.content} contentRef={contentDivRef} />}
        {/* Edit button for user messages */}
        {isUser && message.content && !isStreaming && (
          <button
            onClick={startEdit}
            className="absolute -bottom-1 -left-1 w-5 h-5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
            title={t("chat.editMessage")}
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
                className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] border cursor-pointer transition-colors ${isUser ? "bg-white/10 border-white/20 text-white/90 hover:bg-white/20" : "bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"}`}
                title={t("chat.clickDownload")}
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
          <>
          <div ref={contentDivRef} className={`prose prose-sm max-w-none ${isUser ? "prose-invert" : "dark:prose-invert"}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]} rehypePlugins={[rehypeRaw, rehypeKatex]} components={mdComponents}>{
              // Strip [Attached file: ...] lines and __S3_DOWNLOAD__ markers from display
              (() => {
                let text = message.content;
                if (message.attachments?.length) text = text.replace(/\n\n\[Attached file:[^\]]*\]/g, "").trim();
                text = text.replace(/__S3_DOWNLOAD__:[^:]+:[^\s"}\]]+/g, "").trim();
                return text;
              })()
            }</ReactMarkdown>
            {showTypingIndicator && (
              <span className="inline-flex items-center gap-1 text-gray-400 text-xs mt-2">
                <Loader2 className="w-3 h-3 animate-spin" /> {t("chat.working")}
              </span>
            )}
          </div>
          {/* S3 download buttons — after message content */}
          {(() => {
            const fullText = message.content || "";
            const downloads = [...(fullText.matchAll(/__S3_DOWNLOAD__:([^:\s"}\]]+):([^\s"}\]]+)/g))];
            if (downloads.length === 0) return null;
            const seen = new Set<string>();
            const unique = downloads.filter(([, key]) => {
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
            return (
              <div className="flex flex-wrap gap-2 mt-3">
                {unique.map(([, key, filename], i) => (
                  <button
                    key={i}
                    onClick={async () => {
                      try {
                        const url = await generateDownloadUrl(key);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = filename;
                        a.click();
                      } catch (err) {
                        console.error("Download failed:", err);
                      }
                    }}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" />
                    {filename}
                  </button>
                ))}
              </div>
            );
          })()}
          </>
        ) : (
          <span className="inline-flex items-center gap-1 text-gray-400 text-sm">
            <Loader2 className="w-3 h-3 animate-spin" /> {t("assistant.thinking")}
          </span>
        )}
      </div>
    </div>
  );
});

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
  const imagesAllowed = !agentId || metadata?.supports_images === true;
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

  // Sync URL agent to store
  useEffect(() => {
    switchAgent(agentId || null, agentName);
  }, [agentId, agentName, switchAgent]);

  // Load agent metadata when target changes
  useEffect(() => {
    if (agentName && agentId) {
      fetchAgentMetadataLight(agentId).then((m) => {
        setMetadata(m);
        // Auto-select agent's default model if set
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
        alert(t("chat.fileTooLarge", { name: file.name }));
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
        alert(t("chat.uploadFailed", { name: file.name }));
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
            {agentId ? agentName : t("agents.metaAgent")}
          </h2>
          <p className="text-xs text-gray-500 truncate">
            {agentId
              ? t("chat.chattingWith", { name: agentName })
              : t("chat.metaSubtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Model selector (custom dropdown) */}
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
        {/* Regenerate button after last assistant message */}
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
        {/* Drag handle to resize input area upward */}
        <div
          onMouseDown={onInputDragStart}
          onDoubleClick={() => setInputHeight(inputHeight > 60 ? 44 : 160)}
          className="h-1 cursor-row-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 border-t border-gray-200 dark:border-gray-700 transition-colors"
          title={t("chat.dragExpand")}
        />
        <div className="px-4 py-2">
        {/* Pasted image previews */}
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
        {/* Attached file previews */}
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
