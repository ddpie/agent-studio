import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, ImageOff } from "lucide-react";
import { getDownloadUrl } from "../../lib/api-client";

type ButtonSize = "sm" | "md";

interface Props {
  s3Key: string;
  filename: string;
  size?: ButtonSize;
}

/**
 * Shared download button that handles S3 presigned GET and distinguishes
 * "object is gone" from transient errors.
 *
 * Can't pre-check with HEAD: S3 presigned URLs bind the HTTP method into the
 * signature, so HEAD against a GET-signed URL returns 403 SignatureDoesNotMatch
 * regardless of whether the object exists. Instead we GET directly. A missing
 * object also returns 403 (S3 conflates 404 with 403 for presigned requests)
 * so we parse the XML body for `<Code>NoSuchKey</Code>` to distinguish the
 * two. Used by both the chat page (`S3DownloadList`) and the Runs page
 * (scheduled-task responses) so both surfaces give the same "file no longer
 * available" feedback instead of silently failing.
 */
export default function S3DownloadButton({ s3Key, filename, size = "sm" }: Props) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<"idle" | "checking" | "gone">("idle");

  const onClick = async () => {
    if (status === "gone") return;
    setStatus("checking");
    try {
      const presigned = await getDownloadUrl(s3Key);
      if (!presigned) throw new Error("no presigned url");
      const resp = await fetch(presigned);
      if (!resp.ok) {
        const body = await resp.text().catch(() => "");
        if (body.includes("<Code>NoSuchKey</Code>") || resp.status === 404) {
          setStatus("gone");
          return;
        }
        throw new Error(`download failed: ${resp.status}`);
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
      setStatus("idle");
    } catch (err) {
      console.error("Download failed:", err);
      setStatus("idle");
    }
  };

  const padding = size === "sm" ? "px-3 py-1.5" : "px-3 py-2";
  const textSize = size === "sm" ? "text-xs" : "text-sm";
  const iconSize = size === "sm" ? "w-3.5 h-3.5" : "w-4 h-4";

  if (status === "gone") {
    return (
      <span
        className={`inline-flex items-center gap-1.5 ${padding} ${textSize} font-medium bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 border border-gray-200 dark:border-gray-700 rounded-lg line-through cursor-not-allowed`}
        title={t("chat.fileUnavailable", "File no longer available")}
      >
        <ImageOff className={iconSize} />
        {filename}
      </span>
    );
  }

  return (
    <button
      onClick={onClick}
      disabled={status === "checking"}
      className={`inline-flex items-center gap-1.5 ${padding} ${textSize} font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/50 transition-colors disabled:opacity-50`}
    >
      <Download className={iconSize} />
      {filename}
    </button>
  );
}
