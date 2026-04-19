import { agentConfig } from "../config";
import { getImageUploadUrl, getAttachmentUploadUrl, uploadWithPresignedPost } from "./api-client";

export async function uploadImageToS3(dataUrl: string): Promise<string> {
  const match = dataUrl.match(/^data:image\/(\w+);base64,(.+)$/);
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

export async function uploadFileToS3(
  file: File,
  sessionId: string
): Promise<{ key: string; url: string }> {
  const presigned = await getAttachmentUploadUrl(file.name, file.type || "application/octet-stream", sessionId);
  await uploadWithPresignedPost({ url: presigned.uploadUrl, fields: presigned.fields }, file);

  return { key: presigned.s3Key, url: "" };
}
/**
 * Build the tool-usage hint injected into chat messages for an attached file.
 *
 * For PDF / spreadsheet / tabular files, the sub-agent has a purpose-built
 * `read_document` builtin that handles extraction. For other file types the
 * generic `s3_read(bucket=..., key=...)` fallback is still advertised.
 */
export function buildAttachmentHint(
  file: { name: string; s3Key: string },
  bucket: string,
): string {
  const lower = file.name.toLowerCase();
  const isDocument =
    lower.endsWith(".pdf") ||
    lower.endsWith(".xlsx") ||
    lower.endsWith(".xlsm") ||
    lower.endsWith(".csv") ||
    lower.endsWith(".tsv");
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
    const pathParts = url.pathname.split("/");
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
