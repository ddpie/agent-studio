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

/** Generate a pre-signed GET URL for downloading a file from S3. Valid for 15 minutes. */
export async function generateDownloadUrl(key: string): Promise<string> {
  const signer = await getSigner();
  const expires = 900; // 15 minutes
  const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);
  url.searchParams.set("X-Amz-Expires", String(expires));

  const signed = await signer.presign({
    method: "GET",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: Object.fromEntries(url.searchParams),
    headers: { Host: url.host },
  }, { expiresIn: expires });

  const result = new URL(`${url.protocol}//${url.hostname}${signed.path}`);
  if (signed.query) {
    for (const [k, v] of Object.entries(signed.query)) {
      if (typeof v === "string") result.searchParams.set(k, v);
    }
  }
  return result.toString();
}
