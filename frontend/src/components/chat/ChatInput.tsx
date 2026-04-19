import { useState, useRef, useCallback, useEffect, useImperativeHandle, forwardRef } from "react";
import { useTranslation } from "react-i18next";
import { Send, Loader2, X, Square, Paperclip, FileText } from "lucide-react";
import { uploadImageToS3, uploadFileToS3, buildAttachmentHint } from "../../lib/s3-utils";
import { agentConfig } from "../../config";

interface ChatInputProps {
  onSend: (content: string, images?: string[], modelId?: string, attachments?: Array<{ name: string; size: number; s3Key: string }>) => void;
  isStreaming: boolean;
  onCancel: () => void;
  imagesAllowed: boolean;
  selectedModel: string;
  inputHeight: number;
  dragHandleProps: { onMouseDown: (e: React.MouseEvent) => void; onDoubleClick: () => void };
  sentMessages: string[];
  activeSessionId: string | null;
}

export interface ChatInputHandle {
  setInput: (text: string) => void;
}

const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(function ChatInput({
  onSend, isStreaming, onCancel, imagesAllowed, selectedModel,
  inputHeight, dragHandleProps, sentMessages, activeSessionId,
}, ref) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  // Each image tracks its upload lifecycle so we can surface failures and
  // refuse to send until every image has a stable S3 URL. Storing only
  // S3 URLs (never raw data URLs) in the final message keeps localStorage
  // bounded — see chat-store sanitize logic.
  const [pendingImages, setPendingImages] = useState<Array<{
    id: string;
    previewUrl: string;           // local data URL, only for inline preview
    s3Url?: string;               // populated when upload succeeds
    status: "uploading" | "success" | "failed";
  }>>([]);
  const [attachedFiles, setAttachedFiles] = useState<Array<{ name: string; size: number; s3Key: string; uploading?: boolean }>>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const savedInputRef = useRef("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevStreamingRef = useRef(isStreaming);

  useImperativeHandle(ref, () => ({ setInput }), []);

  // Focus textarea when streaming ends
  useEffect(() => {
    if (prevStreamingRef.current && !isStreaming) {
      textareaRef.current?.focus();
    }
    prevStreamingRef.current = isStreaming;
  }, [isStreaming]);

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
          const id = crypto.randomUUID();
          setPendingImages((prev) => [...prev, { id, previewUrl: dataUrl, status: "uploading" }]);
          try {
            const s3Url = await uploadImageToS3(dataUrl);
            setPendingImages((prev) => prev.map((img) =>
              img.id === id ? { ...img, s3Url, status: "success" } : img
            ));
          } catch (err) {
            console.error("Image upload failed:", err);
            setPendingImages((prev) => prev.map((img) =>
              img.id === id ? { ...img, status: "failed" } : img
            ));
          }
        };
        reader.readAsDataURL(file);
      }
    }
  }, [imagesAllowed]);

  const removeImage = (id: string) => {
    setPendingImages((prev) => prev.filter((img) => img.id !== id));
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

    // Require every pending image to have finished uploading successfully.
    // Failed or still-uploading images block the send so we never embed
    // raw data URLs into the persisted message history.
    if (pendingImages.some((img) => img.status !== "success")) {
      alert(t("chat.imageUploadIncomplete", "Some images are still uploading or failed. Please wait or remove them."));
      return;
    }

    let messageText = input.trim();
    const fileAttachments = attachedFiles.filter((f) => f.s3Key);
    if (fileAttachments.length > 0) {
      const bucket = agentConfig.s3Bucket;
      for (const f of fileAttachments) {
        messageText += `\n\n[Attached file: ${f.name} (${(f.size / 1024).toFixed(1)}KB) — ${buildAttachmentHint(f, bucket)}]`;
      }
    }

    const imageUrlsToSend = pendingImages.length > 0
      ? pendingImages.map((img) => img.s3Url!) // guarded by status check above
      : undefined;
    onSend(messageText, imageUrlsToSend, selectedModel, fileAttachments.length > 0 ? fileAttachments : undefined);
    setInput("");
    setPendingImages([]);
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
        if (historyIdx === -1) savedInputRef.current = input;
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
    <div>
      <div
        {...dragHandleProps}
        className="h-1 cursor-row-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 border-t border-gray-200 dark:border-gray-700 transition-colors"
        title={t("chat.dragExpand")}
      />
      <div className="px-4 py-2">
        {pendingImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pendingImages.map((img) => (
              <div key={img.id} className="relative group">
                <img
                  src={img.previewUrl}
                  className={`w-16 h-16 rounded-lg object-cover border ${
                    img.status === "failed"
                      ? "border-red-500 opacity-50"
                      : "border-gray-200 dark:border-gray-700"
                  }`}
                />
                {img.status === "uploading" && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/30 rounded-lg">
                    <Loader2 className="w-4 h-4 text-white animate-spin" />
                  </div>
                )}
                {img.status === "failed" && (
                  <div
                    className="absolute inset-0 flex items-center justify-center rounded-lg"
                    title={t("chat.imageUploadFailed", "Upload failed — remove and retry")}
                  >
                    <span className="text-[10px] font-semibold text-red-600 bg-white/90 px-1 rounded">
                      {t("chat.uploadFailedShort", "Failed")}
                    </span>
                  </div>
                )}
                <button onClick={() => removeImage(img.id)} className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
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
                  <button onClick={() => setAttachedFiles((prev) => prev.filter((_, i) => i !== idx))} className="text-gray-300 hover:text-red-500 ml-0.5">
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          <input ref={fileInputRef} type="file" accept=".csv,.tsv,.json,.txt,.md,.py,.yaml,.yml,.xml,.html,.log,.sql" multiple className="hidden" onChange={handleFileSelect} />
          <button type="button" onClick={() => fileInputRef.current?.click()} className="p-2.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 flex-shrink-0" title={t("chat.attachFile")}>
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
            <button type="button" onClick={onCancel} className="p-2.5 rounded-xl bg-red-500 text-white hover:bg-red-600 flex-shrink-0" title={t("chat.stopGenerating")}>
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()} className="p-2.5 rounded-xl bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0">
              <Send className="w-4 h-4" />
            </button>
          )}
        </form>
      </div>
    </div>
  );
});

export default ChatInput;
