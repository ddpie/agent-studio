/**
 * Stream Bridge — connects AgentCore SSE stream to the Feishu CardKit replier.
 *
 * Reuses the iterateAgentLines pattern from lambda/a2a-proxy/handler.mjs.
 * Batches updates: flush every 500ms OR 100 chars of new content.
 * 850s watchdog prevents Lambda timeout (900s max).
 */

import { createHash } from "node:crypto";
import { InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";

const WATCHDOG_MS = 850_000; // 850s — leave 50s buffer before 900s Lambda timeout
const FLUSH_INTERVAL_MS = 500;
const FLUSH_CHAR_THRESHOLD = 100;

/**
 * Build the runtime session ID (deterministic per user/agent/chat tuple).
 */
export function buildSessionId({ channelId, chatId, userId, agentId }) {
  const input = `${channelId}|${chatId}|${userId}|${agentId}`;
  return createHash("sha256").update(input).digest("hex").slice(0, 48);
}

/**
 * Invoke AgentCore and stream the response to the replier.
 *
 * @param {object} params
 * @param {import("@aws-sdk/client-bedrock-agentcore").BedrockAgentCoreClient} params.agentcore - AgentCore client
 * @param {string} params.agentArn - Agent runtime ARN
 * @param {object} params.payload - Request payload for the agent
 * @param {string} params.sessionId - Runtime session ID
 * @param {object} params.replier - FeishuReplier instance
 * @param {object} params.ctx - StreamContext from createStreamingReply
 * @returns {Promise<string>} Full accumulated response text
 */
export async function streamToReplier({ agentcore, agentArn, payload, sessionId, replier, ctx, truncatedMsg = "\n\n[Response truncated due to timeout]" }) {
  const agentResp = await agentcore.send(new InvokeAgentRuntimeCommand({
    agentRuntimeArn: agentArn,
    qualifier: "DEFAULT",
    payload: Buffer.from(JSON.stringify(payload)),
    runtimeSessionId: sessionId,
  }));

  let accumulated = "";
  let lastFlushedLength = 0;
  let sequence = 2; // 1 was used for "Thinking..."
  let timedOut = false;

  const startTime = Date.now();

  // Flush timer — sends accumulated text to card at intervals
  let flushPending = false;
  const flushTimer = setInterval(() => {
    flushPending = true;
  }, FLUSH_INTERVAL_MS);

  try {
    for await (const line of iterateAgentLines(agentResp)) {
      // Watchdog check
      if (Date.now() - startTime > WATCHDOG_MS) {
        accumulated += truncatedMsg;
        timedOut = true;
        break;
      }

      const chunk = parseChunk(line);
      if (!chunk) continue;

      if (chunk.kind === "text") {
        // Handle internal control markers — render some, skip others
        if (chunk.text.includes('"__')) {
          try {
            const obj = JSON.parse(chunk.text);
            if (obj.__tool === "start" && obj.name) {
              accumulated += `\n🔧 *调用 ${obj.name}...*\n`;
            } else if (obj.__tool === "result" && obj.name) {
              accumulated += `\n> ✅ ${obj.name} 完成\n`;
            }
            // __tool end, __keepalive, __auto_continue, __error, __file_content, __models → skip
          } catch { /* not valid JSON with __, skip */ }
          continue;
        }
        accumulated += chunk.text;
      } else if (chunk.kind === "keepalive") {
        continue;
      } else if (chunk.kind === "tool") {
        // Already parsed as tool in parseChunk — render inline
        if (chunk.phase === "start" && chunk.name) {
          accumulated += `\n🔧 *调用 ${chunk.name}...*\n`;
        } else if (chunk.phase === "end" && chunk.name) {
          accumulated += `\n> ✅ ${chunk.name} 完成\n`;
        }
      }

      // Flush if interval elapsed or char threshold reached
      const newChars = accumulated.length - lastFlushedLength;
      if (flushPending || newChars >= FLUSH_CHAR_THRESHOLD) {
        await replier.appendContent(ctx, accumulated, sequence++);
        lastFlushedLength = accumulated.length;
        flushPending = false;
      }
    }

    // Final flush of any remaining content
    if (accumulated.length > lastFlushedLength) {
      await replier.appendContent(ctx, accumulated, sequence++);
    }
  } finally {
    clearInterval(flushTimer);
  }

  // Update context sequence for finalize
  ctx.sequence = sequence;

  return accumulated;
}

/**
 * Yield one upstream SSE `data: ...` line at a time.
 * Mirrors the pattern from lambda/a2a-proxy/handler.mjs.
 */
async function* iterateAgentLines(agentResp) {
  const stream = agentResp.response;
  if (!stream) return;

  if (Symbol.asyncIterator in stream) {
    let buffer = "";
    for await (const chunk of stream) {
      if (!(chunk instanceof Uint8Array || Buffer.isBuffer(chunk))) continue;
      buffer += Buffer.from(chunk).toString("utf-8");
      let idx;
      while ((idx = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 1);
        if (!line) continue;
        yield line.startsWith("data: ") ? line.slice(6).trim() : line;
      }
    }
    if (buffer.trim()) {
      const line = buffer.trim();
      yield line.startsWith("data: ") ? line.slice(6).trim() : line;
    }
  }
}

/**
 * Parse a single SSE line into a typed chunk.
 */
function parseChunk(line) {
  if (!line) return null;

  // Keepalive comments
  if (line.startsWith(":")) {
    return { kind: "keepalive" };
  }

  // JSON objects — could be tool markers
  if (line.startsWith("{")) {
    try {
      const obj = JSON.parse(line);
      if (obj && typeof obj === "object") {
        if (obj.__tool === "start") {
          return { kind: "tool", phase: "start", name: obj.name || "" };
        }
        if (obj.__tool === "end" || obj.__tool === "result") {
          return { kind: "tool", phase: "end", name: obj.name || "" };
        }
        if (obj.__keepalive) {
          return { kind: "keepalive" };
        }
      }
      // Other JSON — treat as text
      return { kind: "text", text: line };
    } catch {
      return { kind: "text", text: line };
    }
  }

  // JSON-quoted string (e.g., "hello world")
  if (line.startsWith("\"") && line.endsWith("\"")) {
    try {
      const decoded = JSON.parse(line);
      if (typeof decoded === "string") return { kind: "text", text: decoded };
    } catch {
      /* fall through */
    }
  }

  return { kind: "text", text: line };
}
