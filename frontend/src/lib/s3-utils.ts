import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";
import { getImageUploadUrl, getAttachmentUploadUrl, uploadWithPresignedPost } from "./api-client";

export type ToolCatalogEntry = {
  id: string; name: string; description: string; category: string; code: string;
  builtin?: boolean; owner?: string; visibility?: string;
};
export type ToolCatalog = Record<string, ToolCatalogEntry>;

let _catalogCache: ToolCatalog | null = null;

/**
 * Fetch the built-in tool catalog from S3. Cached for the session.
 */
export async function fetchToolCatalog(forceRefresh = false): Promise<ToolCatalog> {
  if (_catalogCache && !forceRefresh) return _catalogCache;

  const url = `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/agents/base/tool-catalog.json`;
  const blobUrl = await fetchSignedS3(url);

  const resp = await fetch(blobUrl);
  const catalog = await resp.json() as ToolCatalog;
  URL.revokeObjectURL(blobUrl);
  _catalogCache = catalog;
  return catalog;
}

/** Invalidate the cached tool catalog so next fetch reads fresh data from S3. */
export function invalidateToolCatalogCache() {
  _catalogCache = null;
}

/** Write tool catalog JSON to S3 (used after tool save/delete to keep S3 in sync). */
export async function writeToolCatalog(catalog: ToolCatalog): Promise<void> {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  const signer = new SignatureV4({
    service: "s3",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const bucket = agentConfig.s3Bucket;
  const key = "agents/base/tool-catalog.json";
  const body = JSON.stringify(catalog, null, 2);
  const url = new URL(`https://s3.${agentConfig.region}.amazonaws.com/${bucket}/${key}`);

  const signed = await signer.sign({
    method: "PUT",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: {},
    headers: {
      Host: url.host,
      "Content-Type": "application/json",
    },
    body,
  });

  const response = await fetch(url.toString(), {
    method: "PUT",
    headers: signed.headers as Record<string, string>,
    body,
  });

  if (!response.ok) throw new Error(`S3 write failed: ${response.status}`);
  _catalogCache = catalog;
}

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
  } catch { /* fallback to original SigV4 path */ }

  // Fallback: use SigV4 signing
  const { credentials } = await fetchAuthSession();
  if (!credentials) return s3Url;

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  const signer = new SignatureV4({
    service: "s3",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const parsedUrl = new URL(s3Url);
  const signed = await signer.sign({
    method: "GET",
    protocol: parsedUrl.protocol,
    hostname: parsedUrl.hostname,
    path: parsedUrl.pathname,
    query: {},
    headers: {
      Host: parsedUrl.host,
    },
  });

  // Fetch as blob with signed headers, then create object URL
  const response = await fetch(s3Url, {
    method: "GET",
    headers: signed.headers as Record<string, string>,
  });

  if (!response.ok) return s3Url;

  const blob = await response.blob();
  return URL.createObjectURL(blob);
}
