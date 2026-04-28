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
 *
 * The optional `mode` selects which Kiro agent handles the turn server-side:
 *   - undefined          → full Meta-Agent with 34 tools (default)
 *   - "skill_edit"       → tool-less sidekick for /skills/:id/edit,
 *                          emits `__file_content:PATH` fenced blocks only
 *   - "agent_edit"       → tool-less sidekick for /agents/:id/edit,
 *                          emits `__field_value:FIELD` fenced blocks only
 */
export async function* invokeMetaAgent(
  prompt: string,
  history: ChatMessage[],
  sessionId?: string,
  onStatus?: StatusCallback,
  images?: string[],
  modelId?: string,
  mode?: "skill_edit" | "agent_edit",
): AsyncGenerator<string> {
  const callerId = await getCallerId();
  const wsId = getWorkspaceId();
  const url = `${API_BASE}/invoke/workspaces/${wsId}/meta-agent`;
  // Read the user's current UI language. The Meta-Agent's auto-continue
  // supervisor uses this to pick the "Continue." (EN) vs "继续。" (ZH)
  // re-prompt text when it detects a premature end_turn. Kept inline
  // rather than a per-caller parameter so every site (chat,
  // skill-assistant, edit-assistant, deploy) gets it for free.
  let language = "en";
  try {
    const { useUISettings } = await import("../stores/ui-settings-store");
    language = useUISettings.getState().language || "en";
  } catch {
    // Non-fatal: fall back to English. Happens in unit tests where the
    // store isn't mounted.
  }
  const body: Record<string, unknown> = {
    prompt,
    history,
    images,
    model_id: modelId,
    caller_id: callerId,
    session_id: sessionId,
    language,
  };
  if (mode) body.mode = mode;
  yield* invokeAgent(url, body, onStatus);
}

export interface KiroModelInfo {
  id: string;
  name?: string;
}

/**
 * One-shot control-plane call: asks the Meta-Agent runtime to surface
 * Kiro's advertised model list. Returns [] on any failure so the caller
 * can fall back to its static list without caring about the reason.
 */
export async function listKiroModels(): Promise<KiroModelInfo[]> {
  const callerId = await getCallerId();
  const wsId = getWorkspaceId();
  const url = `${API_BASE}/invoke/workspaces/${wsId}/meta-agent`;
  const body = { action: "list_models", caller_id: callerId, prompt: "" };
  try {
    for await (const chunk of invokeAgent(url, body)) {
      if (chunk.includes('"__models"')) {
        try {
          const parsed = JSON.parse(chunk);
          if (Array.isArray(parsed.__models)) return parsed.__models;
        } catch {
          // fall through
        }
      }
    }
  } catch {
    // Any error — cold start, auth, Kiro down — we just return empty.
    return [];
  }
  return [];
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
      // Guard against Lambda runtime crashes that return 200 + JSON body
      // (module load failures, uncaught exceptions before streamifyResponse
      // commits SSE headers). Without this the body falls through to
      // parseSSEStream which yields nothing — the spinner hangs forever.
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("text/event-stream")) {
        const text = await response.text();
        let msg = text;
        try {
          const parsed = JSON.parse(text);
          if (parsed?.errorMessage) {
            msg = parsed.errorMessage;
          }
        } catch { /* keep raw text */ }
        throw new Error(`Agent backend error (non-SSE response): ${msg.slice(0, 500)}`);
      }
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

// Watchdog: if upstream is silent longer than this, cancel the reader and
// throw. 2× the 30s keepalive cadence — real network stalls exceed that,
// but a healthy stream never does.
const SSE_IDLE_WATCHDOG_MS = 60_000;

async function* parseSSEStream(response: Response): AsyncGenerator<string> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      // Race the next chunk against the idle watchdog so a silent-drop
      // doesn't leave the UI spinner stuck forever. Must clear the timer
      // on every successful read — a naked `setTimeout` per iteration
      // accumulates into dozens of live timers over a long conversation.
      let timerId: ReturnType<typeof setTimeout> | null = null;
      const idleTimer = new Promise<never>((_, rej) => {
        timerId = setTimeout(() => rej(new Error("stream_idle_timeout")), SSE_IDLE_WATCHDOG_MS);
      });
      let raced;
      try {
        raced = await Promise.race([reader.read(), idleTimer]);
      } finally {
        if (timerId !== null) clearTimeout(timerId);
      }
      const { done, value } = raced;
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
          // Keep-alive sentinel emitted by Meta-Agent runtime every 15s to
          // prevent CloudFront's 60s origin idle timeout during long
          // tool_use argument generation. Drop silently — it carries no
          // user-visible content.
          if (content.includes('"__keepalive"')) continue;
          // Top-level control frames. Each one is emitted by Meta-Agent as
          // a standalone JSON object, so a strict `startsWith` + parse
          // check gives us a false-positive-proof recognizer — a model
          // that happens to quote the JSON inside prose will have the
          // text arrive as part of a larger text chunk and won't trigger.
          const trimmed = content.trimStart();
          // Auto-continue: supervisor fires before re-prompting Kiro
          // when a turn ends without [[TASK_COMPLETE]]. Forwarded to
          // chat-store so it can render an "auto-continuing (N/M)"
          // badge. Forward as-is (matching the __tool convention) —
          // chat-store parses the JSON itself.
          if (trimmed.startsWith('{"__auto_continue"')) {
            try {
              JSON.parse(trimmed);  // validate shape; on fail, fall through
              yield content;
              continue;
            } catch (e) {
              if (!(e instanceof SyntaxError)) throw e;
              // malformed — fall through to normal yield
            }
          }
          // Error frame: Meta-Agent emits this on ACPError / exception
          // paths just before returning. Without recognition the JSON
          // leaks into message content and the outer try/catch never
          // runs — the spinner stays on even though SSE is done.
          if (trimmed.startsWith('{"__error"')) {
            try {
              const parsed = JSON.parse(content);
              if (parsed && typeof parsed.__error === "string") {
                throw new Error(parsed.__error);
              }
            } catch (e) {
              if (e instanceof SyntaxError) {
                // Fall through — not actually a JSON frame.
              } else {
                throw e;
              }
            }
          }
          yield content;
        }
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
}
