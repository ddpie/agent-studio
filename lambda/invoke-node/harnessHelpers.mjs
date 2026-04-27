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
import { randomUUID } from "node:crypto";

export function doubleUuid() {
  return (randomUUID() + randomUUID()).replaceAll("-", "");
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
