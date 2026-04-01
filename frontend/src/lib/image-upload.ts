import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

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
