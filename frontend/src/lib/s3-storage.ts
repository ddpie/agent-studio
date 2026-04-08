/**
 * S3 download helper — delegates to Lambda presigned URL API.
 * SigV4 direct-signing code removed in Phase 2 cleanup.
 */

/** Download a file from S3 via presigned URL and return a blob URL for browser download. */
export async function generateDownloadUrl(key: string): Promise<string> {
  const { getDownloadUrl } = await import("./api-client");
  const presignedUrl = await getDownloadUrl(key);
  if (!presignedUrl) throw new Error(`Failed to get download URL for: ${key}`);

  const resp = await fetch(presignedUrl);
  if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}
