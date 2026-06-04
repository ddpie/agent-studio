import { agentConfig } from "../config";
import { getImageUploadUrl, getAttachmentUploadUrl, uploadWithPresignedPost } from "./api-client";

export async function uploadImageToS3(dataUrl: string): Promise<string> {
  const match = /^data:image\/(\w+);base64,(.+)$/.exec(dataUrl);
  if (!match) throw new Error("Invalid image data URL");

  const [, ext, base64Data] = match;
  const bytes = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
  const blob = new Blob([bytes], { type: `image/${ext}` });
  const filename = `${crypto.randomUUID()}.${ext}`;

  const presigned = await getImageUploadUrl(filename, `image/${ext}`);
  await uploadWithPresignedPost({ url: presigned.uploadUrl, fields: presigned.fields }, blob);

  // Return full S3 URL so ImageLightbox can fetch via presigned URL
  return `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/${presigned.s3Key}`;
}

// Canonicalise content-type by file extension. Browsers are wildly
// inconsistent here — Chrome/Safari/Firefox on Windows vs. macOS vs.
// Linux report different `file.type` values for the same `.json`:
// "application/json", "text/json", "application/octet-stream", or
// empty string. Backend uses a strict whitelist so the mismatches get
// rejected. Mapping here ensures the caller always sends the MIME
// string the backend recognises.
const EXT_TO_MIME: Record<string, string> = {
  json: "application/json",
  csv: "text/csv",
  tsv: "text/csv",
  txt: "text/plain",
  md: "text/markdown",
  log: "text/plain",
  py: "text/plain",
  yaml: "text/plain",
  yml: "text/plain",
  xml: "text/plain",
  html: "text/plain",
  sql: "text/plain",
  pdf: "application/pdf",
};

function canonicalContentType(file: File): string {
  const ext = ((/\.([a-z0-9]+)$/.exec(file.name.toLowerCase())) || [])[1] || "";
  if (ext && EXT_TO_MIME[ext]) return EXT_TO_MIME[ext];
  return file.type || "application/octet-stream";
}

export async function uploadFileToS3(
  file: File,
  sessionId: string
): Promise<{ key: string; url: string }> {
  const presigned = await getAttachmentUploadUrl(file.name, canonicalContentType(file), sessionId);
  await uploadWithPresignedPost({ url: presigned.uploadUrl, fields: presigned.fields }, file);

  return { key: presigned.s3Key, url: "" };
}
/**
 * Build the tool-usage hint injected into chat messages for an attached file.
 *
 * For PDF / spreadsheet / tabular files, the agent has a purpose-built
 * `read_document` builtin that handles extraction. For other file types the
 * generic `s3_read(bucket=..., key=...)` fallback is still advertised.
 */
export function buildAttachmentHint(
  file: { name: string; s3Key: string },
  bucket: string,
): string {
  const lower = file.name.toLowerCase();
  // read_document handles anything the agent_template's builtin can
  // extract/stream — PDFs, spreadsheets, CSV/TSV, and the plain-text
  // family (json/txt/md/log/source code). Script skills don't need the
  // hint because run_skill_script auto-prefetches S3 attachment keys
  // into the sandbox, but the orchestrator path still uses this.
  const isDocument =
    lower.endsWith(".pdf") ||
    lower.endsWith(".xlsx") ||
    lower.endsWith(".xlsm") ||
    lower.endsWith(".csv") ||
    lower.endsWith(".tsv") ||
    lower.endsWith(".json") ||
    lower.endsWith(".txt") ||
    lower.endsWith(".md") ||
    lower.endsWith(".log");
  if (isDocument) {
    return `call read_document(file_key="${file.s3Key}") to read this file (fallback: s3_read(bucket="${bucket}", key="${file.s3Key}"))`;
  }
  return `use s3_read(bucket="${bucket}", key="${file.s3Key}") to read this file`;
}

export async function fetchSignedS3(s3Url: string): Promise<string> {
  // If it's already a data URL, return as-is
  if (s3Url.startsWith("data:")) return s3Url;

  try {
    const url = new URL(s3Url);
    // new URL() percent-encodes non-ASCII path segments — e.g. a key with
    // Chinese characters comes out as `outputs/abc_%E6%B5%81.png`. The
    // backend presign API expects the raw key (utf-8 bytes, no encoding),
    // so decode the pathname back before slicing.
    const rawPath = decodeURIComponent(url.pathname);
    const pathParts = rawPath.split("/");
    const bucketIdx = pathParts.indexOf(agentConfig.s3Bucket);
    if (bucketIdx >= 0) {
      const key = pathParts.slice(bucketIdx + 1).join("/");
      const { getDownloadUrl } = await import("./api-client");
      const presignedUrl = await getDownloadUrl(key);
      if (presignedUrl) {
        const response = await fetch(presignedUrl);
        if (response.ok) {
          const blob = await response.blob();
          return URL.createObjectURL(blob);
        }
      }
    }
  } catch { /* fallback to original URL */ }

  return s3Url;
}
