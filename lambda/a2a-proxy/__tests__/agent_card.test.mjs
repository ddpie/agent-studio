import { test } from "node:test";
import assert from "node:assert/strict";
import { buildPublicAgentCard, buildExtendedAgentCard } from "../lib/agent_card.mjs";

test("public agent card shape matches A2A spec (v0.3.0)", () => {
  const card = buildPublicAgentCard({
    name: "testAgent",
    runtimeId: "testAgent-abc123",
    description: "test agent",
    version: "7",
    a2aBaseUrl: "https://example.cloudfront.net/a2a/agents/testAgent-abc123",
  });

  assert.equal(card.name, "testAgent");
  assert.equal(card.version, "7");
  assert.equal(card.protocolVersion, "0.3.0");
  assert.equal(card.preferredTransport, "JSONRPC");
  assert.equal(card.url, "https://example.cloudfront.net/a2a/agents/testAgent-abc123");

  assert.ok(card.securitySchemes);
  assert.equal(card.securitySchemes.bearerAuth.type, "http");
  assert.equal(card.securitySchemes.bearerAuth.scheme, "bearer");
  assert.equal(card.securitySchemes.bearerAuth.bearerFormat, "Agent Studio API Key");

  assert.ok(Array.isArray(card.security));
  assert.ok(card.security[0].bearerAuth);

  assert.equal(card.capabilities.extendedAgentCard, true);
  assert.equal(card.capabilities.streaming, true);

  assert.ok(Array.isArray(card.defaultInputModes));
  assert.ok(Array.isArray(card.skills));
  assert.ok(card.skills.length >= 1);
});

test("meta-agent card has orchestration skills", () => {
  const card = buildPublicAgentCard({
    name: "meta",
    runtimeId: "m1",
    description: "",
    version: "1",
    a2aBaseUrl: "https://x/a2a/meta-agent",
    kind: "meta-agent",
  });
  const skillIds = card.skills.map((s) => s.id);
  assert.ok(skillIds.includes("agent_lifecycle"));
});

test("extended card adds runtimeArn + caller", () => {
  const base = buildPublicAgentCard({
    name: "a",
    runtimeId: "a",
    description: "",
    version: "1",
    a2aBaseUrl: "https://x/a2a/agents/a",
  });
  const ext = buildExtendedAgentCard(base, {
    runtimeArn: "arn:aws:bedrock-agentcore:us-east-1:000:runtime/a",
    userId: "u1",
    workspaceId: "w1",
  });
  assert.equal(ext.runtimeArn, "arn:aws:bedrock-agentcore:us-east-1:000:runtime/a");
  assert.equal(ext.caller.userId, "u1");
  assert.equal(ext.caller.workspaceId, "w1");
  assert.ok(ext.provider);
});
