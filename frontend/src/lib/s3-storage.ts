/**
 * Generic S3 JSON read/write with SigV4 signing.
 * Reusable for assistant history, drafts, and other JSON data.
 */
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

const BUCKET = agentConfig.s3Bucket;
const S3_ENDPOINT = `https://s3.${agentConfig.region}.amazonaws.com`;

async function getSigner() {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  return new SignatureV4({
    service: "s3",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });
}

/** Read a JSON object from S3. Returns null if not found. */
export async function readJsonFromS3<T = unknown>(key: string): Promise<T | null> {
  try {
    const signer = await getSigner();
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);

    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      method: "GET",
      headers: signed.headers as Record<string, string>,
    });

    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

/** Write a JSON object to S3. */
export async function writeJsonToS3(key: string, data: unknown): Promise<boolean> {
  try {
    const signer = await getSigner();
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);
    const body = JSON.stringify(data);

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

    const resp = await fetch(url.toString(), {
      method: "PUT",
      headers: signed.headers as Record<string, string>,
      body,
    });

    return resp.ok;
  } catch {
    return false;
  }
}

/** Read a binary object from S3. Returns null if not found. */
export async function readBinaryFromS3(key: string): Promise<ArrayBuffer | null> {
  try {
    const signer = await getSigner();
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);

    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      method: "GET",
      headers: signed.headers as Record<string, string>,
    });

    if (!resp.ok) return null;
    return resp.arrayBuffer();
  } catch {
    return null;
  }
}

/** Delete an object from S3. */
export async function deleteFromS3(key: string): Promise<boolean> {
  try {
    const signer = await getSigner();
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);

    const signed = await signer.sign({
      method: "DELETE",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      method: "DELETE",
      headers: signed.headers as Record<string, string>,
    });

    return resp.ok;
  } catch {
    return false;
  }
}

/** List objects under a prefix. Returns keys. */
export async function listS3Keys(prefix: string): Promise<string[]> {
  try {
    const signer = await getSigner();
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}`);
    url.searchParams.set("prefix", prefix);
    url.searchParams.set("list-type", "2");

    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      method: "GET",
      headers: signed.headers as Record<string, string>,
    });

    if (!resp.ok) return [];
    const xml = await resp.text();
    const keys: string[] = [];
    const re = /<Key>([^<]+)<\/Key>/g;
    let m;
    while ((m = re.exec(xml)) !== null) keys.push(m[1]);
    return keys;
  } catch {
    return [];
  }
}

/** Download a file from S3 via presigned URL and return a blob URL for browser download. */
export async function generateDownloadUrl(key: string): Promise<string> {
  // Try presigned URL via Lambda API first
  try {
    const { getDownloadUrl } = await import("./api-client");
    const presignedUrl = await getDownloadUrl(key);
    if (presignedUrl) {
      const resp = await fetch(presignedUrl);
      if (resp.ok) {
        const blob = await resp.blob();
        return URL.createObjectURL(blob);
      }
    }
  } catch { /* fallback to SigV4 */ }

  // Fallback: SigV4 direct (legacy path, works when Lambda unavailable)
  const signer = await getSigner();
  const hostname = `s3.${agentConfig.region}.amazonaws.com`;
  const rawPath = `/${BUCKET}/${key}`;
  const encodedPath = `/${BUCKET}/${key.split("/").map(s => encodeURIComponent(s)).join("/")}`;

  const signed = await signer.sign({
    method: "GET",
    protocol: "https:",
    hostname,
    path: rawPath,
    query: {},
    headers: { Host: hostname },
  });

  const resp = await fetch(`https://${hostname}${encodedPath}`, {
    method: "GET",
    headers: signed.headers as Record<string, string>,
  });

  if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}
