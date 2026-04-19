import { test } from "node:test";
import assert from "node:assert/strict";
import { parseRoute, isValidId } from "../lib/routes.mjs";

test("parses agent card public path", () => {
  const r = parseRoute("/a2a/agents/claudewatch-abc/.well-known/agent-card.json");
  assert.deepEqual(r, { type: "agent-card-public", agentId: "claudewatch-abc" });
});

test("parses agent rpc path", () => {
  const r = parseRoute("/a2a/agents/abc123");
  assert.deepEqual(r, { type: "agent-rpc", agentId: "abc123" });
});

test("parses extended card path", () => {
  const r = parseRoute("/a2a/agents/x/authenticatedExtendedCard");
  assert.deepEqual(r, { type: "agent-card-extended", agentId: "x" });
});

test("parses meta-agent routes", () => {
  assert.equal(parseRoute("/a2a/meta-agent/.well-known/agent-card.json").type, "meta-card-public");
  assert.equal(parseRoute("/a2a/meta-agent/authenticatedExtendedCard").type, "meta-card-extended");
  assert.equal(parseRoute("/a2a/meta-agent").type, "meta-rpc");
});

test("rejects invalid agent id", () => {
  assert.equal(parseRoute("/a2a/agents/bad;id"), null);
});

test("health route", () => {
  assert.equal(parseRoute("/a2a/health").type, "health");
});

test("isValidId rejects bad input", () => {
  assert.equal(isValidId(""), false);
  assert.equal(isValidId("x y"), false);
  assert.equal(isValidId("a".repeat(200)), false);
});
