import crypto from "node:crypto";

/**
 * Translate between A2A wire format and our internal AgentCore HTTP payload.
 *
 * A2A types here match a2a-sdk v0.3.x — shapes are inlined rather than
 * depending on the Python package because this is a Node.js lambda.
 */

export function a2aToInternalPayload(params, { userId }) {
  const msg = params?.message || {};
  const parts = Array.isArray(msg.parts) ? msg.parts : [];
  const text = parts
    .filter((p) => p.kind === "text" || p.type === "text")
    .map((p) => p.text || "")
    .join("");
  return {
    prompt: text,
    history: [],
    session_id: msg.contextId || null,
    caller_id: userId,
  };
}

/**
 * Convert an internal streaming chunk to an A2A event body. The
 * external client receives these as JSON-RPC `result` payloads
 * delivered via SSE (one event per chunk).
 */
export function internalChunkToA2AEvent(chunk, { taskId, contextId }) {
  if (chunk.kind === "text") {
    return {
      kind: "message",
      role: "agent",
      messageId: cryptoRandomId(),
      parts: [{ kind: "text", text: chunk.text }],
      taskId,
      contextId,
    };
  }
  if (chunk.kind === "tool") {
    const txt = chunk.phase === "start"
      ? `Calling tool: ${chunk.name}`
      : `Tool ${chunk.name} ${chunk.phase}`;
    return {
      kind: "status-update",
      taskId,
      contextId,
      status: {
        state: "working",
        message: {
          role: "agent",
          kind: "message",
          messageId: cryptoRandomId(),
          parts: [{ kind: "text", text: txt }],
        },
      },
      final: false,
    };
  }
  if (chunk.kind === "final") {
    return {
      kind: "status-update",
      taskId,
      contextId,
      status: { state: "completed" },
      final: true,
    };
  }
  return null;
}

export function cryptoRandomId() {
  return [...crypto.randomBytes(6)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
