import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

const BUCKET = agentConfig.s3Bucket;
const S3_ENDPOINT = `https://s3.${agentConfig.region}.amazonaws.com`;

export interface AgentMetadata {
  name: string;
  display_name: string;
  description: string;
  model_id: string;
  system_prompt: string;
  welcome_message: string;
  suggestions: string[];
  tools: string[];
  template_id: string;
  supports_images: boolean;
  created_at: string;
}

/**
 * Fetch agent metadata.json from S3 using SigV4.
 * Uses agentId as the S3 path key.
 */
export async function fetchAgentMetadata(agentId: string): Promise<AgentMetadata | null> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return null;

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

    const key = `agents/${agentId}/metadata.json`;
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`);

    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const response = await fetch(url.toString(), {
      method: "GET",
      headers: signed.headers as Record<string, string>,
    });

    if (!response.ok) {
      console.error(`fetchAgentMetadata failed: ${response.status} for ${key}`);
      return null;
    }
    return response.json();
  } catch (err) {
    console.error("fetchAgentMetadata error:", err);
    return null;
  }
}
