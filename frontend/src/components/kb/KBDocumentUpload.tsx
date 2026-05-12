import { useState, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Upload, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { getAttachmentUploadUrl, uploadWithPresignedPost } from "../../lib/api-client";

interface Props {
  kbId: string;
  onUploaded: (stagingKey: string, filename: string) => Promise<void>;
}

type UploadStatus = "idle" | "uploading" | "success" | "error";

interface FileProgress {
  name: string;
  status: UploadStatus;
  error?: string;
}

const ALLOWED_EXTENSIONS = ["pdf", "md", "txt", "html", "csv", "docx", "xlsx", "pptx"];
const MAX_SIZE = 50 * 1024 * 1024; // 50MB

function getContentType(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  const map: Record<string, string> = {
    pdf: "application/pdf",
    md: "text/markdown",
    txt: "text/plain",
    html: "text/html",
    csv: "text/csv",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  };
  return map[ext] || "application/octet-stream";
}

export default function KBDocumentUpload({ kbId, onUploaded }: Props) {
  const { t } = useTranslation();
  const [dragOver, setDragOver] = useState(false);
  const [uploads, setUploads] = useState<FileProgress[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const processFiles = useCallback(
    async (files: File[]) => {
      const validFiles = files.filter((f) => {
        const ext = f.name.split(".").pop()?.toLowerCase() || "";
        return ALLOWED_EXTENSIONS.includes(ext) && f.size <= MAX_SIZE;
      });

      if (validFiles.length === 0) return;

      const progress: FileProgress[] = validFiles.map((f) => ({
        name: f.name,
        status: "uploading" as UploadStatus,
      }));
      setUploads(progress);

      for (let i = 0; i < validFiles.length; i++) {
        const file = validFiles[i];
        try {
          // Get presigned URL using the attachment upload endpoint
          const sessionId = `kb-${kbId}`;
          const contentType = getContentType(file.name);
          const presigned = await getAttachmentUploadUrl(file.name, contentType, sessionId);

          // Upload to S3
          await uploadWithPresignedPost(
            { url: presigned.uploadUrl, fields: presigned.fields },
            file
          );

          // Notify parent with the staging key
          await onUploaded(presigned.s3Key, file.name);

          setUploads((prev) =>
            prev.map((p, idx) =>
              idx === i ? { ...p, status: "success" as UploadStatus } : p
            )
          );
        } catch (e: any) {
          setUploads((prev) =>
            prev.map((p, idx) =>
              idx === i
                ? { ...p, status: "error" as UploadStatus, error: e.message }
                : p
            )
          );
        }
      }

      // Clear successful uploads after a delay
      setTimeout(() => {
        setUploads((prev) => prev.filter((p) => p.status === "error"));
      }, 3000);
    },
    [kbId, onUploaded]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const files = Array.from(e.dataTransfer.files);
      processFiles(files);
    },
    [processFiles]
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      processFiles(files);
      if (inputRef.current) inputRef.current.value = "";
    },
    [processFiles]
  );

  const isUploading = uploads.some((u) => u.status === "uploading");

  return (
    <div className="space-y-2">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !isUploading && inputRef.current?.click()}
        className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
          dragOver
            ? "border-blue-400 dark:border-blue-500 bg-blue-50 dark:bg-blue-900/20"
            : "border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600"
        } ${isUploading ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        <Upload className="w-6 h-6 mx-auto mb-2 text-gray-400 dark:text-gray-500" />
        <p className="text-sm text-gray-600 dark:text-gray-400">
          {t("kb.dragHint")}
        </p>
        <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
          {t("kb.formatHint")}
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(",")}
          onChange={handleInputChange}
          className="hidden"
        />
      </div>

      {/* Upload progress list */}
      {uploads.length > 0 && (
        <div className="space-y-1">
          {uploads.map((u, idx) => (
            <div
              key={idx}
              className="flex items-center gap-2 px-3 py-1.5 text-xs rounded-lg bg-gray-50 dark:bg-gray-800"
            >
              {u.status === "uploading" && (
                <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
              )}
              {u.status === "success" && (
                <CheckCircle className="w-3.5 h-3.5 text-green-500" />
              )}
              {u.status === "error" && (
                <AlertCircle className="w-3.5 h-3.5 text-red-500" />
              )}
              <span className="text-gray-700 dark:text-gray-300 truncate flex-1">
                {u.name}
              </span>
              {u.error && (
                <span className="text-red-500 dark:text-red-400 text-[10px] truncate max-w-[200px]">
                  {u.error}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
