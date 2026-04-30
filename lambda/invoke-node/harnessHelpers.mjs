/**
 * Helpers for the harness invoke path.
 *
 * - doubleUuid: AgentCore harness requires runtimeSessionId >= 33 chars.
 *   Concatenating two UUID4s (hex-stripped) gives a 64-char hex string,
 *   well above the threshold and collision-resistant.
 *
 * - historyToHarnessMessages: convert our frontend's {role, content:string}
 *   history + current prompt into the harness-native
 *   {role, content: [{text}]} message shape.
 */
import { createHash, randomUUID } from "node:crypto";

export function doubleUuid() {
  return (randomUUID() + randomUUID()).replaceAll("-", "");
}

/**
 * Build a stable harness session id from (agentId, userId).
 *
 * Harness Memory attributes messages to `actorId + sessionId`; using a
 * fresh random id per invoke would make every turn look like a new
 * user, defeating memory recall. sha256 the tuple → hex → first 64
 * chars so it satisfies the >=33-char length requirement.
 *
 * Tradeoff: one long-lived "session" per (agent, user) pair — messages
 * from the same user to the same agent will share memory forever. For
 * true multi-session UX we'd need the frontend to thread a sessionId,
 * which isn't wired yet. Good enough for MVP.
 */
export function stableHarnessSessionId(agentId, userId) {
  return createHash("sha256")
    .update(`${agentId}|${userId || "anonymous"}`)
    .digest("hex");
}

export function historyToHarnessMessages(history, currentPrompt) {
  const msgs = Array.isArray(history)
    ? history.map((h) => ({
        role: h.role,
        content: [{ text: h.content }],
      }))
    : [];
  msgs.push({ role: "user", content: [{ text: currentPrompt }] });
  return msgs;
}
