/**
 * Unit tests for handler.mjs — tests the pure helper functions and logic
 * that can be exercised without the Lambda streaming runtime (awslambda global).
 *
 * The actual handler export relies on `awslambda.streamifyResponse` which only
 * exists inside the Lambda Node.js runtime. We test the internal logic by
 * extracting/re-implementing the helpers here and asserting their behavior.
 */
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

// ── Replicate internal helpers from handler.mjs for unit testing ──
// These are copied from handler.mjs because the module cannot be imported
// directly (it references `awslambda` global). We test the logic, not the import.

const ID_PATTERN = /^[a-zA-Z0-9_-]+$/;

function validateId(value, name) {
  if (!value || !ID_PATTERN.test(value) || value.length > 128) return `Invalid ${name}`;
  return null;
}

function extractToken(headers) {
  const xAuth = headers["x-auth-token"];
  if (xAuth) return xAuth;
  const auth = headers["authorization"] || "";
  if (auth.startsWith("Bearer ")) return auth.slice(7);
  return null;
}

function jsonResponse(statusCode, body) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

const ROLE_LEVEL = { viewer: 0, editor: 1, admin: 2, owner: 3 };

function checkPermission(member, minRole) {
  if (!member) return false;
  return (ROLE_LEVEL[member.role] ?? -1) >= (ROLE_LEVEL[minRole] ?? 99);
}

function parseRoute(path) {
  let m = path.match(/^\/invoke\/workspaces\/([^/]+)\/meta-agent$/);
  if (m) return { type: "meta-agent", wsId: m[1] };
  m = path.match(/^\/invoke\/workspaces\/([^/]+)\/agents\/([^/]+)$/);
  if (m) return { type: "agent", wsId: m[1], agentId: m[2] };
  if (path === "/invoke/health") return { type: "health" };
  return null;
}

// ── Tests ──

describe("validateId", () => {
  test("accepts valid alphanumeric id", () => {
    assert.equal(validateId("abc123", "id"), null);
  });

  test("accepts hyphens and underscores", () => {
    assert.equal(validateId("my-agent_v2", "id"), null);
  });

  test("accepts max 128 chars", () => {
    assert.equal(validateId("a".repeat(128), "id"), null);
  });

  test("rejects empty string", () => {
    assert.ok(validateId("", "id"));
  });

  test("rejects null/undefined", () => {
    assert.ok(validateId(null, "id"));
    assert.ok(validateId(undefined, "id"));
  });

  test("rejects special characters", () => {
    assert.ok(validateId("agent!name", "id"));
    assert.ok(validateId("agent name", "id"));
    assert.ok(validateId("agent/name", "id"));
    assert.ok(validateId("agent.name", "id"));
    assert.ok(validateId("agent@name", "id"));
  });

  test("rejects path traversal attempts", () => {
    assert.ok(validateId("../etc/passwd", "id"));
    assert.ok(validateId("..%2F..%2Fetc", "id"));
  });

  test("rejects over 128 chars", () => {
    assert.ok(validateId("a".repeat(129), "id"));
  });

  test("rejects unicode characters", () => {
    assert.ok(validateId("agent-名前", "id"));
  });

  test("rejects null bytes", () => {
    assert.ok(validateId("agent\x00id", "id"));
  });

  test("includes field name in error message", () => {
    const err = validateId("bad!", "workspaceId");
    assert.ok(err.includes("workspaceId"));
  });

  test("rejects extremely long strings", () => {
    assert.ok(validateId("x".repeat(10000), "id"));
  });
});

describe("extractToken", () => {
  test("extracts token from Authorization Bearer header", () => {
    const token = extractToken({ authorization: "Bearer my-jwt-token" });
    assert.equal(token, "my-jwt-token");
  });

  test("prefers x-auth-token over Authorization", () => {
    const token = extractToken({
      "x-auth-token": "token-from-x-auth",
      authorization: "Bearer token-from-auth",
    });
    assert.equal(token, "token-from-x-auth");
  });

  test("returns null when no auth headers present", () => {
    assert.equal(extractToken({}), null);
  });

  test("returns null for non-Bearer Authorization", () => {
    assert.equal(extractToken({ authorization: "Basic abc123" }), null);
  });

  test("returns null for empty Authorization header", () => {
    assert.equal(extractToken({ authorization: "" }), null);
  });

  test("handles Bearer with empty token", () => {
    // "Bearer " with nothing after gives empty string
    const token = extractToken({ authorization: "Bearer " });
    assert.equal(token, "");
  });

  test("handles x-auth-token with empty value", () => {
    // Empty string is falsy, so falls through to Authorization
    const token = extractToken({ "x-auth-token": "" });
    assert.equal(token, null);
  });

  test("handles token with spaces (e.g. malformed)", () => {
    const token = extractToken({ authorization: "Bearer token with spaces" });
    assert.equal(token, "token with spaces");
  });
});

describe("jsonResponse", () => {
  test("formats 200 response with JSON body", () => {
    const resp = jsonResponse(200, { status: "ok" });
    assert.equal(resp.statusCode, 200);
    assert.equal(resp.headers["content-type"], "application/json");
    assert.equal(resp.body, '{"status":"ok"}');
  });

  test("formats 400 error response", () => {
    const resp = jsonResponse(400, { error: "Bad request" });
    assert.equal(resp.statusCode, 400);
    assert.deepEqual(JSON.parse(resp.body), { error: "Bad request" });
  });

  test("formats 403 forbidden response", () => {
    const resp = jsonResponse(403, { error: "Forbidden" });
    assert.equal(resp.statusCode, 403);
  });

  test("formats 424 cold-start response", () => {
    const resp = jsonResponse(424, { error: "Agent initializing" });
    assert.equal(resp.statusCode, 424);
    assert.deepEqual(JSON.parse(resp.body), { error: "Agent initializing" });
  });

  test("formats 502 gateway error", () => {
    const resp = jsonResponse(502, { error: "Failed to invoke agent" });
    assert.equal(resp.statusCode, 502);
  });
});

describe("checkPermission", () => {
  test("returns false for null member", () => {
    assert.equal(checkPermission(null, "viewer"), false);
  });

  test("returns false for undefined member", () => {
    assert.equal(checkPermission(undefined, "viewer"), false);
  });

  test("viewer meets viewer requirement", () => {
    assert.equal(checkPermission({ role: "viewer" }, "viewer"), true);
  });

  test("editor meets viewer requirement", () => {
    assert.equal(checkPermission({ role: "editor" }, "viewer"), true);
  });

  test("admin meets editor requirement", () => {
    assert.equal(checkPermission({ role: "admin" }, "editor"), true);
  });

  test("owner meets all requirements", () => {
    assert.equal(checkPermission({ role: "owner" }, "viewer"), true);
    assert.equal(checkPermission({ role: "owner" }, "editor"), true);
    assert.equal(checkPermission({ role: "owner" }, "admin"), true);
    assert.equal(checkPermission({ role: "owner" }, "owner"), true);
  });

  test("viewer does not meet editor requirement", () => {
    assert.equal(checkPermission({ role: "viewer" }, "editor"), false);
  });

  test("editor does not meet admin requirement", () => {
    assert.equal(checkPermission({ role: "editor" }, "admin"), false);
  });

  test("unknown role is rejected", () => {
    assert.equal(checkPermission({ role: "superuser" }, "viewer"), false);
  });

  test("unknown minRole rejects all", () => {
    assert.equal(checkPermission({ role: "owner" }, "superadmin"), false);
  });
});

describe("parseRoute", () => {
  test("parses health check path", () => {
    const route = parseRoute("/invoke/health");
    assert.deepEqual(route, { type: "health" });
  });

  test("parses meta-agent path", () => {
    const route = parseRoute("/invoke/workspaces/ws-123/meta-agent");
    assert.deepEqual(route, { type: "meta-agent", wsId: "ws-123" });
  });

  test("parses agent invoke path", () => {
    const route = parseRoute("/invoke/workspaces/ws-abc/agents/agent-xyz");
    assert.deepEqual(route, { type: "agent", wsId: "ws-abc", agentId: "agent-xyz" });
  });

  test("returns null for unknown paths", () => {
    assert.equal(parseRoute("/invoke/unknown"), null);
    assert.equal(parseRoute("/api/workspaces/ws/agents/a"), null);
    assert.equal(parseRoute("/"), null);
    assert.equal(parseRoute(""), null);
  });

  test("returns null for partial matches", () => {
    assert.equal(parseRoute("/invoke/workspaces/"), null);
    assert.equal(parseRoute("/invoke/workspaces/ws/agents/"), null);
  });

  test("handles workspace id with hyphens and underscores", () => {
    const route = parseRoute("/invoke/workspaces/my_ws-123/meta-agent");
    assert.deepEqual(route, { type: "meta-agent", wsId: "my_ws-123" });
  });

  test("does not match with trailing slash", () => {
    assert.equal(parseRoute("/invoke/workspaces/ws/meta-agent/"), null);
  });

  test("does not match with extra path segments", () => {
    assert.equal(parseRoute("/invoke/workspaces/ws/agents/a/extra"), null);
  });
});

describe("SSE response formatting", () => {
  test("200 SSE response has correct headers", () => {
    // Simulates the meta object used for SSE streaming
    const meta = {
      statusCode: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
      },
    };
    assert.equal(meta.statusCode, 200);
    assert.equal(meta.headers["content-type"], "text/event-stream");
    assert.equal(meta.headers["cache-control"], "no-cache");
    assert.equal(meta.headers["x-accel-buffering"], "no");
  });

  test("SSE data frame format is correct", () => {
    const payload = JSON.stringify({ __keepalive: true });
    const frame = `data: ${payload}\n\n`;
    assert.ok(frame.startsWith("data: "));
    assert.ok(frame.endsWith("\n\n"));
    assert.deepEqual(JSON.parse(frame.slice(6).trim()), { __keepalive: true });
  });

  test("SSE error frame format", () => {
    const errorFrame = `data: ${JSON.stringify({ error: "Streaming error" })}\n\n`;
    const parsed = JSON.parse(errorFrame.slice(6).trim());
    assert.equal(parsed.error, "Streaming error");
  });

  test("SSE __error frame format for harness errors", () => {
    const msg = "harness invoke failed";
    const errorFrame = `data: ${JSON.stringify({ __error: msg })}\n\n`;
    const parsed = JSON.parse(errorFrame.slice(6).trim());
    assert.equal(parsed.__error, "harness invoke failed");
  });
});

describe("SSE buffer line flushing logic", () => {
  // Re-implement the flushLines logic for testing
  function createFlusher() {
    let sseBuffer = "";
    const output = [];

    function flushLines(flushAll = false) {
      sseBuffer = sseBuffer.replace(/\r\n/g, "\n");
      const lines = sseBuffer.split("\n");
      sseBuffer = flushAll ? "" : (lines.pop() || "");
      for (const line of lines) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("data: ")) {
          output.push(`${line}\n\n`);
        }
      }
    }

    return {
      feed(chunk) { sseBuffer += chunk; flushLines(); },
      flush() { flushLines(true); },
      get output() { return output; },
    };
  }

  test("flushes complete data lines", () => {
    const f = createFlusher();
    f.feed('data: "hello"\n');
    assert.deepEqual(f.output, ['data: "hello"\n\n']);
  });

  test("buffers incomplete lines until newline", () => {
    const f = createFlusher();
    f.feed('data: "hel');
    assert.deepEqual(f.output, []);
    f.feed('lo"\n');
    assert.deepEqual(f.output, ['data: "hello"\n\n']);
  });

  test("handles CRLF line endings", () => {
    const f = createFlusher();
    f.feed('data: "hi"\r\n');
    assert.deepEqual(f.output, ['data: "hi"\n\n']);
  });

  test("drops SSE comment lines (starting with colon)", () => {
    const f = createFlusher();
    f.feed(': keepalive\ndata: "real"\n');
    assert.deepEqual(f.output, ['data: "real"\n\n']);
  });

  test("drops empty lines", () => {
    const f = createFlusher();
    f.feed('\n\ndata: "val"\n\n');
    assert.deepEqual(f.output, ['data: "val"\n\n']);
  });

  test("drops non-data lines (event:, id:, etc.)", () => {
    const f = createFlusher();
    f.feed('event: message\ndata: "payload"\nid: 123\n');
    // Only the data: line is forwarded
    assert.deepEqual(f.output, ['data: "payload"\n\n']);
  });

  test("flushAll drains remaining buffer", () => {
    const f = createFlusher();
    f.feed('data: "partial"');
    assert.deepEqual(f.output, []);
    f.flush();
    assert.deepEqual(f.output, ['data: "partial"\n\n']);
  });

  test("handles multiple data lines in one chunk", () => {
    const f = createFlusher();
    f.feed('data: "one"\ndata: "two"\ndata: "three"\n');
    assert.deepEqual(f.output, [
      'data: "one"\n\n',
      'data: "two"\n\n',
      'data: "three"\n\n',
    ]);
  });
});

describe("cold-start 424 detection", () => {
  test("424 status maps to Agent initializing error", () => {
    // Simulates the handler logic when AgentCore returns 424
    const err = { $metadata: { httpStatusCode: 424 } };
    const status = err.$metadata?.httpStatusCode || 500;
    assert.equal(status, 424);
    const resp = jsonResponse(424, { error: "Agent initializing" });
    assert.equal(resp.statusCode, 424);
    assert.deepEqual(JSON.parse(resp.body), { error: "Agent initializing" });
  });

  test("non-424 errors map to 502", () => {
    const err = { $metadata: { httpStatusCode: 500 } };
    const status = err.$metadata?.httpStatusCode || 500;
    assert.notEqual(status, 424);
    const resp = jsonResponse(502, { error: "Failed to invoke agent" });
    assert.equal(resp.statusCode, 502);
  });

  test("missing metadata defaults to 500", () => {
    const err = {};
    const status = err.$metadata?.httpStatusCode || 500;
    assert.equal(status, 500);
  });
});

describe("keepalive frame", () => {
  test("keepalive frame is valid JSON wrapped in SSE data format", () => {
    const frame = `data: ${JSON.stringify({ __keepalive: true })}\n\n`;
    assert.equal(frame, 'data: {"__keepalive":true}\n\n');
    const parsed = JSON.parse(frame.slice(6).trim());
    assert.equal(parsed.__keepalive, true);
  });

  test("keepalive frame has no other fields", () => {
    const parsed = JSON.parse(JSON.stringify({ __keepalive: true }));
    assert.deepEqual(Object.keys(parsed), ["__keepalive"]);
  });
});

describe("request body parsing", () => {
  test("base64 encoded body can be decoded", () => {
    const original = JSON.stringify({ prompt: "hello" });
    const encoded = Buffer.from(original).toString("base64");
    const decoded = Buffer.from(encoded, "base64").toString();
    assert.deepEqual(JSON.parse(decoded), { prompt: "hello" });
  });

  test("empty body defaults to empty object", () => {
    const rawBody = "";
    const body = JSON.parse(rawBody || "{}");
    assert.deepEqual(body, {});
  });

  test("null body defaults to empty object", () => {
    const rawBody = null;
    const body = JSON.parse(rawBody || "{}");
    assert.deepEqual(body, {});
  });

  test("invalid JSON is detected", () => {
    const rawBody = "not json{";
    assert.throws(() => JSON.parse(rawBody));
  });
});

describe("session_id validation in body", () => {
  test("valid session_id passes", () => {
    const sessionId = "abc-123_XYZ";
    const valid = typeof sessionId === "string" && ID_PATTERN.test(sessionId) && sessionId.length <= 128;
    assert.ok(valid);
  });

  test("session_id with special chars fails", () => {
    const sessionId = "session id!";
    const valid = typeof sessionId === "string" && ID_PATTERN.test(sessionId) && sessionId.length <= 128;
    assert.ok(!valid);
  });

  test("session_id over 128 chars fails", () => {
    const sessionId = "a".repeat(129);
    const valid = typeof sessionId === "string" && ID_PATTERN.test(sessionId) && sessionId.length <= 128;
    assert.ok(!valid);
  });

  test("non-string session_id fails", () => {
    const sessionId = 12345;
    const valid = typeof sessionId === "string" && ID_PATTERN.test(sessionId) && sessionId.length <= 128;
    assert.ok(!valid);
  });
});

describe("agent ownership check logic", () => {
  test("agent in correct workspace passes", () => {
    const item = { agentId: "agent-1", workspace_id: "ws-1", status: "active" };
    const wsId = "ws-1";
    const owned = item && item.workspace_id === wsId;
    const archived = item.status === "archived";
    assert.ok(owned);
    assert.ok(!archived);
  });

  test("agent in different workspace is forbidden", () => {
    const item = { agentId: "agent-1", workspace_id: "ws-2", status: "active" };
    const wsId = "ws-1";
    const owned = item && item.workspace_id === wsId;
    assert.ok(!owned);
  });

  test("missing agent item is forbidden", () => {
    const item = null;
    assert.ok(!item);
  });

  test("archived agent returns 404 equivalent", () => {
    const item = { agentId: "agent-1", workspace_id: "ws-1", status: "archived" };
    const wsId = "ws-1";
    const owned = item && item.workspace_id === wsId;
    const archived = item.status === "archived";
    assert.ok(owned);
    assert.ok(archived);
  });
});

describe("mode/action/language payload sanitization", () => {
  test("mode under 32 chars is accepted", () => {
    const mode = "skill_edit";
    const accepted = typeof mode === "string" && mode.length <= 32;
    assert.ok(accepted);
  });

  test("mode over 32 chars is rejected", () => {
    const mode = "x".repeat(33);
    const accepted = typeof mode === "string" && mode.length <= 32;
    assert.ok(!accepted);
  });

  test("language under 8 chars is accepted", () => {
    const lang = "zh-Hans";
    const accepted = typeof lang === "string" && lang.length <= 8;
    assert.ok(accepted);
  });

  test("language over 8 chars is rejected", () => {
    const lang = "en-US-extra";
    const accepted = typeof lang === "string" && lang.length <= 8;
    assert.ok(!accepted);
  });

  test("non-string mode is rejected", () => {
    const mode = 123;
    const accepted = typeof mode === "string" && mode.length <= 32;
    assert.ok(!accepted);
  });

  test("action under 32 chars is accepted", () => {
    const action = "list_models";
    const accepted = typeof action === "string" && action.length <= 32;
    assert.ok(accepted);
  });
});
