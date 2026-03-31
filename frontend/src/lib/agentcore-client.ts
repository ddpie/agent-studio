import { fetchAuthSession } from "aws-amplify/auth";
import { getCurrentUser } from "aws-amplify/auth";
import { agentConfig } from "../config";

const ACCOUNT_ID = agentConfig.accountId;
const MAX_INIT_RETRIES = 3;
const INIT_RETRY_DELAY_MS = 5000;

async function getCallerId(): Promise<string> {
  try {
    const { username } = await getCurrentUser();
    return username;
  } catch {
    return "unknown";
  }
}

function getEndpoint(agentArn: string) {
  return `https://bedrock-agentcore.${agentConfig.region}.amazonaws.com/runtimes/${encodeURIComponent(agentArn)}/invocations?qualifier=DEFAULT`;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** Callback for status updates (e.g., "Agent is initializing...") */
export type StatusCallback = (status: string | null) => void;

/**
 * Invoke the Meta-Agent with full conversation history.
 */
export async function* invokeMetaAgent(
  prompt: string,
  history: ChatMessage[],
  sessionId?: string,
  onStatus?: StatusCallback,
  images?: string[],
  modelId?: string
): AsyncGenerator<string> {
  const callerId = await getCallerId();
  yield* invokeAgent(agentConfig.metaAgentArn, JSON.stringify({ prompt, history, images, model_id: modelId, caller_id: callerId }), sessionId, onStatus);
}

/**
 * Invoke any Agent on AgentCore Runtime by ARN or ID.
 */
export async function* invokeAgentById(
  agentId: string,
  prompt: string,
  history: ChatMessage[],
  sessionId?: string,
  onStatus?: StatusCallback,
  images?: string[],
  modelId?: string
): AsyncGenerator<string> {
  const arn = `arn:aws:bedrock-agentcore:${agentConfig.region}:${ACCOUNT_ID}:runtime/${agentId}`;
  yield* invokeAgent(arn, JSON.stringify({ prompt, history, images, model_id: modelId }), sessionId, onStatus);
}

async function signAndFetch(agentArn: string, body: string, sessionId?: string): Promise<Response> {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  const signer = new SignatureV4({
    service: "bedrock-agentcore",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const url = new URL(getEndpoint(agentArn));
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    Host: url.host,
  };

  if (sessionId) {
    headers["x-amz-bedrock-agentcore-runtime-session-id"] = sessionId;
  }

  const signed = await signer.sign({
    method: "POST",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: Object.fromEntries(url.searchParams),
    headers,
    body,
  });

  return fetch(url.toString(), {
    method: "POST",
    headers: signed.headers as Record<string, string>,
    body,
  });
}

/**
 * Core: invoke an AgentCore Runtime agent with SigV4 signing.
 * Auto-retries on 424 (cold start initialization timeout).
 */
async function* invokeAgent(
  agentArn: string,
  body: string,
  sessionId?: string,
  onStatus?: StatusCallback
): AsyncGenerator<string> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= MAX_INIT_RETRIES; attempt++) {
    const response = await signAndFetch(agentArn, body, sessionId);

    if (response.ok) {
      onStatus?.(null);
      yield* parseSSEStream(response);
      return;
    }

    // 424 = cold start timeout, retry
    if (response.status === 424 && attempt < MAX_INIT_RETRIES) {
      onStatus?.(`Agent is initializing... (attempt ${attempt + 1}/${MAX_INIT_RETRIES})`);
      await new Promise((r) => setTimeout(r, INIT_RETRY_DELAY_MS));
      continue;
    }

    const text = await response.text();
    lastError = new Error(`AgentCore error ${response.status}: ${text}`);
    break;
  }

  onStatus?.(null);
  throw lastError || new Error("Failed to invoke agent");
}

async function* parseSSEStream(response: Response): AsyncGenerator<string> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        let content = line.slice(6).trim();
        if (content.startsWith('"') && content.endsWith('"')) {
          content = JSON.parse(content);
        }
        if (content) yield content;
      }
    }
  }
}
