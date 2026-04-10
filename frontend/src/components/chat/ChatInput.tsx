import { useState, useRef, useCallback, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Send, Loader2, X, Square, Paperclip, FileText } from "lucide-react";
import { uploadImageToS3, uploadFileToS3 } from "../../lib/s3-utils";
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

export default function ChatInput({
  onSend, isStreaming, onCancel, imagesAllowed, selectedModel,
  inputHeight, dragHandleProps, sentMessages, activeSessionId,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [pastedImages, setPastedImages] = useState<string[]>([]);
  const [uploadedImageUrls, setUploadedImageUrls] = useState<string[]>([]);
  const [attachedFiles, setAttachedFiles] = useState<Array<{ name: string; size: number; s3Key: string; uploading?: boolean }>>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const savedInputRef = useRef("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const prevStreamingRef = useRef(isStreaming);

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
    onSend(messageText, imageUrlsToSend, selectedModel, fileAttachments.length > 0 ? fileAttachments : undefined);
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
        {pastedImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {pastedImages.map((src, idx) => (
              <div key={idx} className="relative group">
                <img src={src} className="w-16 h-16 rounded-lg object-cover border border-gray-200 dark:border-gray-700" />
                <button onClick={() => removeImage(idx)} className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
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
}
