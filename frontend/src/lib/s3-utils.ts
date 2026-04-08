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
  await uploadWithPresignedPost(presigned, blob);

  return presigned.key;
}

export async function uploadFileToS3(
  file: File,
  _sessionId: string
): Promise<{ key: string; url: string }> {
  const presigned = await getAttachmentUploadUrl(file.name, file.type || "application/octet-stream");
  await uploadWithPresignedPost(presigned, file);

  return { key: presigned.key, url: "" };
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
