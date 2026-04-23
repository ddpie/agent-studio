import { useState, useRef, memo } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, FileText, Pencil, Download } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import rehypeKatex from "rehype-katex";
import { useChatStore, type Message } from "../../stores/chat-store";
import { fetchSignedS3, buildAttachmentHint } from "../../lib/s3-utils";
import { agentConfig } from "../../config";
import ImageLightbox from "../ui/ImageLightbox";
import CopyButtons from "./CopyButtons";
import { mdComponents } from "./CodeBlock";
import ToolCallDetails from "./ToolCallDetails";
import S3DownloadList from "./S3DownloadList";

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), ["className"]],
    span: [...(defaultSchema.attributes?.span || []), ["className"]],
    div: [...(defaultSchema.attributes?.div || []), ["className"]],
  },
};

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
    const cleanContent = message.attachments?.length
      ? message.content.replace(/\n\n\[Attached file:[^\]]*\]/g, "").trim()
      : message.content;
    setEditText(cleanContent);
    setEditing(true);
  };

  const submitEdit = () => {
    if (editText.trim() && editText !== message.content) {
      let finalText = editText.trim();
      if (message.attachments?.length) {
        const bucket = agentConfig.s3Bucket;
        for (const f of message.attachments) {
          finalText += `\n\n[Attached file: ${f.name} (${(f.size / 1024).toFixed(1)}KB) — ${buildAttachmentHint(f, bucket)}]`;
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
            className="w-full px-4 py-3 rounded-2xl border-2 border-blue-400 dark:border-blue-500 text-sm focus:outline-none resize-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          />
          <div className="flex justify-end gap-1.5 mt-1">
            <button onClick={() => setEditing(false)} className="text-[11px] px-2 py-0.5 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded">{t("common.cancel")}</button>
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
        {isUser && message.content && !isStreaming && (
          <button
            onClick={startEdit}
            className="absolute -bottom-1 -left-1 w-5 h-5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
            title={t("chat.editMessage")}
          >
            <Pencil className="w-2.5 h-2.5 text-gray-400 dark:text-gray-500" />
          </button>
        )}
        {message.images && message.images.length > 0 && (
          <div className="flex gap-2 flex-wrap mb-2">
            {message.images.map((src, i) => (
              <ImageLightbox key={i} src={src} />
            ))}
          </div>
        )}
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
                <span className={isUser ? "text-white/50" : "text-gray-400 dark:text-gray-500"}>{(f.size / 1024).toFixed(1)}KB</span>
                <Download className="w-3 h-3 flex-shrink-0 opacity-50" />
              </button>
            ))}
          </div>
        )}
        {(() => {
          const hasContent = !!message.content;
          const hasTools = !!(message.toolCalls && message.toolCalls.length);
          const hasDownloads = !!(message.s3Downloads && message.s3Downloads.length);
          if (!hasContent && !hasTools && !hasDownloads) {
            return (
              <span className="inline-flex items-center gap-1 text-gray-400 dark:text-gray-500 text-sm">
                <Loader2 className="w-3 h-3 animate-spin" /> {t("assistant.thinking")}
              </span>
            );
          }
          return (
            <>
              {hasContent && (
                <div ref={contentDivRef} className={`prose prose-sm max-w-none ${isUser ? "prose-invert [&_*]:text-white" : "dark:prose-invert"}`}>
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]} rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], rehypeKatex]} components={mdComponents}>{
                    (() => {
                      let text = message.content;
                      if (message.attachments?.length) text = text.replace(/\n\n\[Attached file:[^\]]*\]/g, "").trim();
                      return text;
                    })()
                  }</ReactMarkdown>
                  {showTypingIndicator && (
                    <span className="inline-flex items-center gap-1 text-gray-400 dark:text-gray-500 text-xs mt-2">
                      <Loader2 className="w-3 h-3 animate-spin" /> {t("chat.working")}
                    </span>
                  )}
                </div>
              )}
              {hasTools && <ToolCallDetails calls={message.toolCalls!} />}
              {hasDownloads && <S3DownloadList downloads={message.s3Downloads!} />}
            </>
          );
        })()}
      </div>
    </div>
  );
});

export default ChatMessage;
