import { test } from "node:test";
import assert from "node:assert/strict";
import { a2aToInternalPayload, internalChunkToA2AEvent } from "../lib/translate.mjs";

test("a2a message/send → internal payload", () => {
  const params = {
    message: {
      role: "user",
      messageId: "m1",
      contextId: "ctx-123",
      parts: [
        { kind: "text", text: "hello " },
        { kind: "text", text: "world" },
      ],
    },
  };
  const p = a2aToInternalPayload(params, { userId: "u1" });
  assert.equal(p.prompt, "hello world");
  assert.equal(p.session_id, "ctx-123");
  assert.equal(p.caller_id, "u1");
});

test("text chunk → A2A Message event", () => {
  const event = internalChunkToA2AEvent(
    { kind: "text", text: "partial reply" },
    { taskId: "t1", contextId: "c1" }
  );
  assert.equal(event.kind, "message");
  assert.equal(event.role, "agent");
  assert.equal(event.parts[0].text, "partial reply");
  assert.equal(event.taskId, "t1");
});

test("__tool start marker → TaskStatusUpdateEvent(working)", () => {
  const ev = internalChunkToA2AEvent(
    { kind: "tool", phase: "start", name: "run_command" },
    { taskId: "t1", contextId: "c1" }
  );
  assert.equal(ev.kind, "status-update");
  assert.equal(ev.status.state, "working");
  assert.equal(ev.status.message.parts[0].text, "Calling tool: run_command");
});

test("final chunk → completed status", () => {
  const ev = internalChunkToA2AEvent({ kind: "final" }, { taskId: "t1", contextId: "c1" });
  assert.equal(ev.kind, "status-update");
  assert.equal(ev.status.state, "completed");
  assert.equal(ev.final, true);
});
