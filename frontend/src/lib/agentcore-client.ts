import { fetchAuthSession } from "aws-amplify/auth";
import { getCurrentUser } from "aws-amplify/auth";
import { agentConfig } from "../config";
import { getWorkspaceId } from "./api-client";

const MAX_INIT_RETRIES = 3;
const INIT_RETRY_DELAY_MS = 5000;
const API_BASE = agentConfig.apiUrl;

async function waitForOnlineOrTimeout(ms = 30_000): Promise<boolean> {
  if (navigator.onLine) return true;
  return new Promise((resolve) => {
    const cleanup = () => {
      window.removeEventListener("online", onOnline);
      clearTimeout(timer);
    };
    const onOnline = () => { cleanup(); resolve(true); };
    const timer = setTimeout(() => { cleanup(); resolve(false); }, ms);
    window.addEventListener("online", onOnline, { once: true });
  });
}

async function getCallerId(): Promise<string> {
  try {
    const { username } = await getCurrentUser();
    return username;
  } catch {
    return "unknown";
  }
}

async function getIdToken(forceRefresh = false): Promise<string> {
  const session = await fetchAuthSession({ forceRefresh });
  const token = session.tokens?.idToken?.toString();
  if (!token) throw new Error("Not authenticated");
  return token;
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
  const wsId = getWorkspaceId();
  const url = `${API_BASE}/invoke/workspaces/${wsId}/meta-agent`;
  const body = { prompt, history, images, model_id: modelId, caller_id: callerId, session_id: sessionId };
  yield* invokeAgent(url, body, onStatus);
}

/**
 * Invoke any Agent on AgentCore Runtime by ID.
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
  const wsId = getWorkspaceId();
  const url = `${API_BASE}/invoke/workspaces/${wsId}/agents/${encodeURIComponent(agentId)}`;
  const body = { prompt, history, images, model_id: modelId, session_id: sessionId };
  yield* invokeAgent(url, body, onStatus);
}

/**
 * Core: invoke via Lambda proxy with JWT auth.
 * Auto-retries on 424 (cold start initialization timeout).
 */
async function* invokeAgent(
  url: string,
  body: Record<string, unknown>,
  onStatus?: StatusCallback
): AsyncGenerator<string> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= MAX_INIT_RETRIES; attempt++) {
    const token = await getIdToken(attempt > 0);
    const bodyStr = JSON.stringify(body);
    // CloudFront OAC requires x-amz-content-sha256 for POST to Lambda Function URL
    const hashBuffer = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(bodyStr));
    const hashHex = Array.from(new Uint8Array(hashBuffer)).map(b => b.toString(16).padStart(2, "0")).join("");
    // Timeout for the initial connection only (not the streaming phase).
    const timeoutController = new AbortController();
    const timeoutId = setTimeout(() => timeoutController.abort(), 60_000);
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-Token": token,
          "x-amz-content-sha256": hashHex,
          Accept: "text/event-stream",
        },
        body: bodyStr,
        signal: timeoutController.signal,
      });
    } catch (netErr) {
      clearTimeout(timeoutId);
      if (attempt === 0 && !navigator.onLine) {
        onStatus?.("Waiting for network...");
        const back = await waitForOnlineOrTimeout(30_000);
        if (back) {
          onStatus?.("Back online. Retrying...");
          continue;
        }
      }
      lastError = netErr as Error;
      break;
    }

    // Connection established — cancel the timeout so it doesn't fire during streaming.
    clearTimeout(timeoutId);

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

    // 403 on first attempt = possible token expiry, force refresh and retry once
    if (response.status === 403 && attempt === 0) {
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

  try {
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
          if (!content) continue;
          // Keep-alive sentinel emitted by Meta-Agent runtime every 30s to
          // prevent CloudFront's 60s origin idle timeout during long
          // tool_use argument generation. Drop silently — it carries no
          // user-visible content.
          if (content.includes('"__keepalive"')) continue;
          yield content;
        }
      }
    }
  } catch (streamErr) {
    yield `\n\n[${(streamErr as Error).message || "stream interrupted"}]\n`;
  } finally {
    await reader.cancel().catch(() => {});
  }
}
