import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

export type ToolCatalogEntry = { id: string; name: string; description: string; category: string; code: string };
export type ToolCatalog = Record<string, ToolCatalogEntry>;

let _catalogCache: ToolCatalog | null = null;

/**
 * Fetch the built-in tool catalog from S3. Cached for the session.
 */
export async function fetchToolCatalog(): Promise<ToolCatalog> {
  if (_catalogCache) return _catalogCache;

  const url = `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/base/tool-catalog.json`;
  const blobUrl = await fetchSignedS3(url);

  const resp = await fetch(blobUrl);
  const catalog = await resp.json() as ToolCatalog;
  URL.revokeObjectURL(blobUrl);
  _catalogCache = catalog;
  return catalog;
}

/**
 * Upload a base64 data URL image to S3 and return the S3 URL.
 * Path: agents/images/{uuid}.{ext}
 */
export async function uploadImageToS3(dataUrl: string): Promise<string> {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  // Parse data URL
  const match = dataUrl.match(/^data:image\/(\w+);base64,(.+)$/);
  if (!match) throw new Error("Invalid image data URL");

  const [, ext, base64Data] = match;
  const bytes = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
  const uuid = crypto.randomUUID();
  const key = `agents/images/${uuid}.${ext}`;

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
  const url = new URL(`https://s3.${agentConfig.region}.amazonaws.com/${bucket}/${key}`);

  const signed = await signer.sign({
    method: "PUT",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: {},
    headers: {
      Host: url.host,
      "Content-Type": `image/${ext}`,
    },
    body: bytes,
  });

  const response = await fetch(url.toString(), {
    method: "PUT",
    headers: signed.headers as Record<string, string>,
    body: bytes,
  });

  if (!response.ok) {
    throw new Error(`S3 upload failed: ${response.status}`);
  }

  // Return the S3 URL
  return `https://s3.${agentConfig.region}.amazonaws.com/${bucket}/${key}`;
}

/**
 * Upload a text file (CSV, JSON, TSV, etc.) to S3 and return the S3 key.
 * Path: attachments/{sessionId}/{filename}
 */
export async function uploadFileToS3(file: File, sessionId: string): Promise<{ key: string; url: string }> {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const bytes = new Uint8Array(await file.arrayBuffer());
  const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_");
  const key = `agents/attachments/${sessionId}/${safeName}`;

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
  const url = new URL(`https://s3.${agentConfig.region}.amazonaws.com/${bucket}/${key}`);

  const signed = await signer.sign({
    method: "PUT",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: {},
    headers: {
      Host: url.host,
      "Content-Type": file.type || "application/octet-stream",
    },
    body: bytes,
  });

  const response = await fetch(url.toString(), {
    method: "PUT",
    headers: signed.headers as Record<string, string>,
    body: bytes,
  });

  if (!response.ok) {
    throw new Error(`S3 upload failed: ${response.status}`);
  }

  return { key, url: url.toString() };
}
export async function fetchSignedS3(s3Url: string): Promise<string> {
  // If it's already a data URL, return as-is
  if (s3Url.startsWith("data:")) return s3Url;

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
