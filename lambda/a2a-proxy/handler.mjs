/**
 * A2A Proxy — Node.js streaming lambda exposing A2A-compliant endpoints
 * for Agent Studio agents.
 *
 * Routes (behind CloudFront /a2a/*):
 *   GET  /a2a/agents/{id}/.well-known/agent-card.json     — public
 *   GET  /a2a/agents/{id}/authenticatedExtendedCard        — bearer
 *   POST /a2a/agents/{id}                                  — JSON-RPC bearer
 *   GET  /a2a/meta-agent/.well-known/agent-card.json
 *   GET  /a2a/meta-agent/authenticatedExtendedCard
 *   POST /a2a/meta-agent
 *   GET  /a2a/health
 *
 * Auth: per-user-per-agent API keys stored as SHA-256 hashes in
 * agent-studio-a2a-keys. Bearer token (via Authorization header or the
 * CloudFront-renamed X-A2A-Authorization) → hash → DDB lookup → agent
 * ownership check → InvokeAgentRuntimeCommand with Lambda exec role
 * SigV4.
 */
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { BedrockAgentCoreControlClient, GetAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore-control";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand } from "@aws-sdk/lib-dynamodb";

import { parseRoute } from "./lib/routes.mjs";
import { extractBearerToken, resolveApiKey } from "./lib/auth.mjs";
import { buildPublicAgentCard, buildExtendedAgentCard } from "./lib/agent_card.mjs";
import { parseRpcRequest, rpcSuccess, rpcError } from "./lib/rpc.mjs";
import { a2aToInternalPayload, internalChunkToA2AEvent, cryptoRandomId } from "./lib/translate.mjs";

const REGION = process.env.AWS_REGION || "us-east-1";
const ACCOUNT_ID = process.env.ACCOUNT_ID || "";
const META_AGENT_ARN = process.env.META_AGENT_ARN || "";
const AGENTS_TABLE = process.env.AGENTS_TABLE || "";
const A2A_KEYS_TABLE = process.env.A2A_KEYS_TABLE || "";
const PUBLIC_HOST = process.env.PUBLIC_HOST || "";

const agentcore = new BedrockAgentCoreClient({ region: REGION });
const control = new BedrockAgentCoreControlClient({ region: REGION });
const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));

function getHeader(headers, name) {
  if (!headers) return null;
  const lower = name.toLowerCase();
  for (const k of Object.keys(headers)) {
    if (k.toLowerCase() === lower) return headers[k];
  }
  return null;
}

function extractToken(headers) {
  // Function URL is AuthType=AWS_IAM; CloudFront OAC signs requests with
  // SigV4 in the Authorization header. To preserve the A2A client's
  // `Authorization: Bearer <key>`, a CloudFront viewer-request Function
  // renames it to `x-a2a-authorization` before OAC signs. Read that
  // first; fall back to Authorization for local-test / non-CDN paths.
  const renamed = getHeader(headers, "x-a2a-authorization");
  if (renamed) return extractBearerToken(renamed);
  return extractBearerToken(getHeader(headers, "authorization"));
}


function deriveA2aHost(event) {
  // PUBLIC_HOST is the CloudFront domain injected by CDK. Prefer it so
  // AgentCards advertise the public URL rather than the underlying
  // Function URL host (which OAC sets as Host for SigV4 signing).
  if (PUBLIC_HOST) return PUBLIC_HOST;
  const forwarded = getHeader(event.headers, "x-forwarded-host");
  if (forwarded) return forwarded;
  const host = getHeader(event.headers, "host");
  if (host) return host;
  return event.requestContext?.domainName || "";
}

async function buildPublicCardForAgent(agentId, event) {
  const resp = await ddb.send(new GetCommand({ TableName: AGENTS_TABLE, Key: { agentId } }));
  if (!resp.Item || resp.Item.status === "archived") return null;
  let runtimeName = agentId;
  let runtimeVersion = "1";
  try {
    const rr = await control.send(new GetAgentRuntimeCommand({ agentRuntimeId: agentId }));
    runtimeName = rr.agentRuntimeName || agentId;
    runtimeVersion = String(rr.agentRuntimeVersion || "1");
  } catch {
    // Non-fatal; fall back to item fields.
    runtimeName = resp.Item.name || agentId;
  }
  const host = deriveA2aHost(event);
  const a2aBaseUrl = `https://${host}/a2a/agents/${agentId}`;
  return buildPublicAgentCard({
    name: runtimeName,
    runtimeId: agentId,
    description: resp.Item.description || "",
    version: runtimeVersion,
    a2aBaseUrl,
    kind: "agent",
  });
}

function buildPublicCardForMeta(event) {
  const host = deriveA2aHost(event);
  const a2aBaseUrl = `https://${host}/a2a/meta-agent`;
  // META_AGENT_ARN looks like arn:aws:bedrock-agentcore:R:A:runtime/<id>
  const runtimeId = META_AGENT_ARN.split("/").pop() || "meta-agent";
  return buildPublicAgentCard({
    name: "Agent Studio Meta-Agent",
    runtimeId,
    description: "Orchestrator agent that creates, updates and deploys Agent Studio agents.",
    version: "1",
    a2aBaseUrl,
    kind: "meta-agent",
  });
}

// Keepalive cadence. W3C SSE spec allows comment lines (`:`-prefixed) to be
// sent at any interval to prevent idle timeouts. 15s is the community
// convention — short enough to stay well under CloudFront's 60s origin
// idle timeout and any TCP-level 60s idle trip on the client side.
const KEEPALIVE_INTERVAL_MS = 15_000;

async function invokeRuntimeUnary({ parsed, internal, agentArn }) {
  // Unchanged: A2A protocol `message/send` → one-shot JSON-RPC response
  // (application/json). Long-running targets must use `message/stream`
  // instead to get keepalive-protected SSE (see invokeRuntimeStream).
  let agentResp;
  try {
    agentResp = await agentcore.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: agentArn,
      qualifier: "DEFAULT",
      payload: Buffer.from(JSON.stringify(internal)),
      runtimeSessionId: internal.session_id || undefined,
    }));
  } catch (err) {
    return { status: 502, body: rpcError(parsed.id, -32000, "Runtime invocation failed", { reason: err.message }) };
  }
  const text = await collectStreamText(agentResp);
  const taskId = cryptoRandomId();
  const result = {
    kind: "message",
    role: "agent",
    messageId: cryptoRandomId(),
    parts: [{ kind: "text", text }],
    taskId,
    contextId: internal.session_id || taskId,
  };
  return { status: 200, body: rpcSuccess(parsed.id, result) };
}

async function invokeRuntimeStream({ parsed, internal, agentArn, responseStream }) {
  let agentResp;
  try {
    agentResp = await agentcore.send(new InvokeAgentRuntimeCommand({
      agentRuntimeArn: agentArn,
      qualifier: "DEFAULT",
      payload: Buffer.from(JSON.stringify(internal)),
      runtimeSessionId: internal.session_id || undefined,
    }));
  } catch (err) {
    const meta = { statusCode: 502, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify(rpcError(parsed.id, -32000, "Runtime invocation failed", { reason: err.message })));
    responseStream.end();
    return;
  }
  const meta = {
    statusCode: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      "x-accel-buffering": "no",
    },
  };
  responseStream = awslambda.HttpResponseStream.from(responseStream, meta);

  const taskId = cryptoRandomId();
  const ctxId = internal.session_id || taskId;

  // Keepalive heartbeat: W3C SSE spec allows comment lines (`:`-prefixed)
  // at any cadence to hold the connection open. CloudFront and most TCP
  // clients trip at 60s of idle on the wire; 15s gives ample safety
  // margin. Without this, a target agent that runs for 100s without
  // emitting a chunk causes the whole A2A call to fail with a network
  // error at the client side even though the backend completed fine.
  const keepaliveTimer = setInterval(() => {
    try { responseStream.write(": keepalive\n\n"); } catch { /* stream dead */ }
  }, 15_000);

  try {
    for await (const line of iterateAgentLines(agentResp)) {
      const chunk = parseInternalChunk(line);
      if (!chunk) continue;
      const a2aEvent = internalChunkToA2AEvent(chunk, { taskId, contextId: ctxId });
      if (!a2aEvent) continue;
      const rpc = rpcSuccess(parsed.id, a2aEvent);
      responseStream.write(`data: ${JSON.stringify(rpc)}\n\n`);
    }
    const finalRpc = rpcSuccess(parsed.id, internalChunkToA2AEvent({ kind: "final" }, { taskId, contextId: ctxId }));
    responseStream.write(`data: ${JSON.stringify(finalRpc)}\n\n`);
  } catch (err) {
    console.error("a2a-proxy stream error:", err);
    responseStream.write(`data: ${JSON.stringify(rpcError(parsed.id, -32002, "Stream interrupted"))}\n\n`);
  } finally {
    clearInterval(keepaliveTimer);
  }
  responseStream.end();
}

/**
 * Yield one upstream SSE `data: ...` line at a time. Mirrors the
 * line-by-line parsing in lambda/invoke-node/handler.mjs.
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

async function collectStreamText(agentResp) {
  let text = "";
  for await (const line of iterateAgentLines(agentResp)) {
    const chunk = parseInternalChunk(line);
    if (chunk?.kind === "text") text += chunk.text;
  }
  return text;
}

/**
 * Our agents emit mixed streams: plain text lines plus JSON markers
 * with `__tool` fields. Translate either into a neutral chunk shape that
 * internalChunkToA2AEvent can map to A2A events.
 */
function parseInternalChunk(line) {
  if (!line) return null;
  // AgentCore emits three kinds of SSE payloads:
  //   1. Plain text tokens (already unquoted)
  //   2. JSON-quoted strings like `"hello"` — decode and treat as text
  //   3. Tool markers like `{"__tool":"start","name":"..."}`
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
      }
      return { kind: "text", text: line };
    } catch {
      return { kind: "text", text: line };
    }
  }
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

async function handleAgentRpc({ route, event, responseStream, method, send, agentArn }) {
  if (method !== "POST") return send(405, { error: "Method not allowed" });

  const token = extractToken(event.headers);
  if (!token) {
    return send(401, { error: "Missing bearer token" }, {
      "content-type": "application/json",
      "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
    });
  }
  const key = await resolveApiKey(ddb, A2A_KEYS_TABLE, token);
  const requiredAgentId = route.type === "meta-rpc" ? "meta-agent" : route.agentId;
  if (!key || key.agentId !== requiredAgentId) {
    return send(401, { error: "Invalid key" }, {
      "content-type": "application/json",
      "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
    });
  }

  const raw = event.isBase64Encoded ? Buffer.from(event.body, "base64").toString() : (event.body || "");
  let envelope;
  try { envelope = JSON.parse(raw); } catch {
    return send(400, rpcError(null, -32700, "Parse error"));
  }
  let parsed;
  try { parsed = parseRpcRequest(envelope); }
  catch (e) { return send(400, rpcError(envelope.id ?? null, -32600, e.message)); }

  const internal = a2aToInternalPayload(parsed.params, { userId: key.userId });
  if (route.type === "meta-rpc") internal.workspace_id = key.workspaceId;

  if (parsed.method === "message/send") {
    const out = await invokeRuntimeUnary({ parsed, internal, agentArn });
    return send(out.status, out.body);
  }
  // message/stream — handled directly on responseStream with keepalive heartbeat
  await invokeRuntimeStream({ parsed, internal, agentArn, responseStream });
}

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const path = event.rawPath || event.requestContext?.http?.path || "";
  const method = event.requestContext?.http?.method || "GET";
  const route = parseRoute(path);

  const send = (statusCode, body, headers = { "content-type": "application/json" }) => {
    responseStream = awslambda.HttpResponseStream.from(responseStream, { statusCode, headers });
    responseStream.write(typeof body === "string" ? body : JSON.stringify(body));
    responseStream.end();
  };

  if (!route) return send(404, { error: "Not found" });
  if (route.type === "health") return send(200, { status: "ok" });

  try {
    if (route.type === "agent-card-public") {
      const card = await buildPublicCardForAgent(route.agentId, event);
      if (!card) return send(404, { error: "Agent not found" });
      return send(200, card, { "content-type": "application/json", "cache-control": "max-age=60" });
    }

    if (route.type === "meta-card-public") {
      return send(200, buildPublicCardForMeta(event), {
        "content-type": "application/json",
        "cache-control": "max-age=60",
      });
    }

    if (route.type === "agent-card-extended") {
      const token = extractToken(event.headers);
      if (!token) return send(401, { error: "Missing bearer token" }, {
        "content-type": "application/json",
        "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
      });
      const key = await resolveApiKey(ddb, A2A_KEYS_TABLE, token);
      if (!key || key.agentId !== route.agentId) {
        return send(401, { error: "Invalid key" }, {
          "content-type": "application/json",
          "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
        });
      }
      const publicCard = await buildPublicCardForAgent(route.agentId, event);
      if (!publicCard) return send(404, { error: "Agent not found" });
      const extended = buildExtendedAgentCard(publicCard, {
        runtimeArn: `arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${route.agentId}`,
        userId: key.userId,
        workspaceId: key.workspaceId,
      });
      return send(200, extended);
    }

    if (route.type === "meta-card-extended") {
      const token = extractToken(event.headers);
      if (!token) return send(401, { error: "Missing bearer token" }, {
        "content-type": "application/json",
        "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
      });
      const key = await resolveApiKey(ddb, A2A_KEYS_TABLE, token);
      if (!key || key.agentId !== "meta-agent") {
        return send(401, { error: "Invalid key" }, {
          "content-type": "application/json",
          "www-authenticate": "Bearer realm=\"Agent Studio A2A\"",
        });
      }
      const publicCard = buildPublicCardForMeta(event);
      const extended = buildExtendedAgentCard(publicCard, {
        runtimeArn: META_AGENT_ARN,
        userId: key.userId,
        workspaceId: key.workspaceId,
      });
      return send(200, extended);
    }

    if (route.type === "agent-rpc") {
      const agentArn = `arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${route.agentId}`;
      return await handleAgentRpc({ route, event, responseStream, method, send, agentArn });
    }

    if (route.type === "meta-rpc") {
      return await handleAgentRpc({ route, event, responseStream, method, send, agentArn: META_AGENT_ARN });
    }

    return send(501, { error: "Not implemented", route: route.type });
  } catch (e) {
    console.error("a2a-proxy error:", e);
    return send(500, { error: "Internal error" });
  }
});
