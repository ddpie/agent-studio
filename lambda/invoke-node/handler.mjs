/**
 * Invoke Lambda — Node.js streaming proxy for AgentCore Runtime.
 *
 * Uses native RESPONSE_STREAM (streamifyResponse) to proxy SSE from AgentCore.
 * JWT auth + workspace permission + agent ownership checks.
 */
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand } from "@aws-sdk/lib-dynamodb";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";
import { createRemoteJWKSet, jwtVerify } from "jose";
import { randomBytes } from "node:crypto";
import { invokeHarness } from "./harnessInvoker.mjs";
import { translateHarnessStream } from "./harnessTranslator.mjs";
import { historyToHarnessMessages, doubleUuid } from "./harnessHelpers.mjs";

const REGION = process.env.AWS_REGION || "us-east-1";
const ACCOUNT_ID = process.env.ACCOUNT_ID || "";
const META_AGENT_ARN = process.env.META_AGENT_ARN || "";
const AGENTS_TABLE = process.env.AGENTS_TABLE || "";
const WORKSPACES_TABLE = process.env.WORKSPACES_TABLE || "";
const COGNITO_USER_POOL_ID = process.env.COGNITO_USER_POOL_ID || "";
const COGNITO_CLIENT_ID = process.env.COGNITO_CLIENT_ID || "";

const JWKS_URL = `https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}/.well-known/jwks.json`;
const ISSUER = `https://cognito-idp.${REGION}.amazonaws.com/${COGNITO_USER_POOL_ID}`;

const agentcore = new BedrockAgentCoreClient({ region: REGION });
const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));
const secretsClient = new SecretsManagerClient({ region: REGION });
const jwks = createRemoteJWKSet(new URL(JWKS_URL));

// In-memory cache of Kiro keys per workspace, keyed by wsId. Lambda
// container reuse saves a Secrets Manager call per turn — KMS + SM is
// ~100ms added to cold + every invoke otherwise. 60s TTL bounds how
// long a stale key can linger after an admin rotates it while still
// cutting the bulk of per-turn SM calls. `null` means "we already
// looked and the secret doesn't exist".
const KIRO_KEY_TTL_MS = 60 * 1000;
const kiroKeyCache = new Map(); // wsId -> { value: string | null, fetchedAt: number }

async function fetchKiroKey(wsId) {
  const cached = kiroKeyCache.get(wsId);
  if (cached && Date.now() - cached.fetchedAt < KIRO_KEY_TTL_MS) {
    return cached.value;
  }
  const secretId = `agent-studio/workspaces/${wsId}/kiro-api-key`;
  try {
    const resp = await secretsClient.send(new GetSecretValueCommand({ SecretId: secretId }));
    const value = resp.SecretString || null;
    kiroKeyCache.set(wsId, { value, fetchedAt: Date.now() });
    return value;
  } catch (err) {
    if (err.name === "ResourceNotFoundException") {
      kiroKeyCache.set(wsId, { value: null, fetchedAt: Date.now() });
      return null;
    }
    throw err;
  }
}

const ROLE_LEVEL = { viewer: 0, editor: 1, admin: 2, owner: 3 };
const ID_PATTERN = /^[a-zA-Z0-9_-]+$/;

// ── Helpers ──

function jsonResponse(statusCode, body) {
  return { statusCode, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
}

function validateId(value, name) {
  if (!value || !ID_PATTERN.test(value) || value.length > 128) return `Invalid ${name}`;
  return null;
}

// 32-hex char trace id + 16-hex span id per W3C trace context spec.
function genTraceId() {
  return randomBytes(16).toString("hex");
}

function genSpanId() {
  return randomBytes(8).toString("hex");
}

// ── Auth ──

async function verifyJwt(token) {
  const { payload } = await jwtVerify(token, jwks, {
    issuer: ISSUER,
    audience: COGNITO_CLIENT_ID,
  });
  if (payload.token_use !== "id") throw new Error("Not an id token");
  return payload;
}

function extractToken(headers) {
  // X-Auth-Token preferred (avoids CloudFront OAC SigV4 conflict)
  const xAuth = headers["x-auth-token"];
  if (xAuth) return xAuth;
  const auth = headers["authorization"] || "";
  if (auth.startsWith("Bearer ")) return auth.slice(7);
  return null;
}

async function getMembership(wsId, userId) {
  const resp = await ddb.send(new GetCommand({
    TableName: WORKSPACES_TABLE,
    Key: { workspaceId: wsId, sk: `MEMBER#${userId}` },
    ConsistentRead: true,
  }));
  return resp.Item || null;
}

function checkPermission(member, minRole) {
  if (!member) return false;
  return (ROLE_LEVEL[member.role] ?? -1) >= (ROLE_LEVEL[minRole] ?? 99);
}

async function authCheck(headers, wsId) {
  const token = extractToken(headers);
  if (!token) return { error: jsonResponse(403, { error: "Forbidden" }) };
  let claims;
  try {
    claims = await verifyJwt(token);
  } catch {
    return { error: jsonResponse(403, { error: "Authentication failed" }) };
  }
  const userId = claims.sub;
  const member = await getMembership(wsId, userId);
  if (!checkPermission(member, "viewer")) {
    return { error: jsonResponse(403, { error: "Forbidden" }) };
  }
  return { userId };
}

async function checkAgentOwnership(agentId, wsId) {
  const resp = await ddb.send(new GetCommand({
    TableName: AGENTS_TABLE,
    Key: { agentId },
  }));
  const item = resp.Item;
  if (!item || item.workspace_id !== wsId) {
    return { error: jsonResponse(403, { error: "Forbidden" }) };
  }
  if (item.status === "archived") {
    return { error: jsonResponse(404, { error: "Agent is archived" }) };
  }
  return { item };
}

// ── Route parsing ──

function parseRoute(path) {
  // POST /invoke/workspaces/{wsId}/meta-agent
  let m = path.match(/^\/invoke\/workspaces\/([^/]+)\/meta-agent$/);
  if (m) return { type: "meta-agent", wsId: m[1] };
  // POST /invoke/workspaces/{wsId}/agents/{agentId}
  m = path.match(/^\/invoke\/workspaces\/([^/]+)\/agents\/([^/]+)$/);
  if (m) return { type: "agent", wsId: m[1], agentId: m[2] };
  // GET /invoke/health
  if (path === "/invoke/health") return { type: "health" };
  return null;
}

// ── Main handler ──

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const path = event.rawPath || event.requestContext?.http?.path || "";
  const method = event.requestContext?.http?.method || "GET";
  const headers = event.headers || {};

  const route = parseRoute(path);

  // Health check
  if (route?.type === "health") {
    const meta = { statusCode: 200, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify({ status: "ok" }));
    responseStream.end();
    return;
  }

  if (!route || method !== "POST") {
    const meta = { statusCode: 404, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify({ error: "Not found" }));
    responseStream.end();
    return;
  }

  // Validate IDs
  const wsIdErr = validateId(route.wsId, "workspaceId");
  if (wsIdErr) {
    const meta = { statusCode: 400, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify({ error: wsIdErr }));
    responseStream.end();
    return;
  }
  if (route.agentId) {
    const agentIdErr = validateId(route.agentId, "agentId");
    if (agentIdErr) {
      const meta = { statusCode: 400, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: agentIdErr }));
      responseStream.end();
      return;
    }
  }

  // Auth
  const auth = await authCheck(headers, route.wsId);
  if (auth.error) {
    const meta = { statusCode: auth.error.statusCode, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(auth.error.body);
    responseStream.end();
    return;
  }

  // Agent ownership check
  let agentItem = null;
  if (route.type === "agent") {
    const check = await checkAgentOwnership(route.agentId, route.wsId);
    if (check.error) {
      const meta = { statusCode: check.error.statusCode, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(check.error.body);
      responseStream.end();
      return;
    }
    agentItem = check.item;
  }

  // Parse body
  let body;
  try {
    const rawBody = event.isBase64Encoded ? Buffer.from(event.body, "base64").toString() : event.body;
    body = JSON.parse(rawBody || "{}");
  } catch {
    const meta = { statusCode: 400, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify({ error: "Invalid JSON body" }));
    responseStream.end();
    return;
  }

  // ── Harness runtime branch ──
  // If the target agent is a harness-runtime agent, route through InvokeHarness.
  // This branch is terminal: it writes the SSE response and returns before
  // the existing zip/meta-agent path runs.
  if (route.type === "agent" && agentItem?.runtime_type === "harness") {
    const harnessArn = agentItem.harness_arn;
    if (!harnessArn) {
      const meta = { statusCode: 500, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: "Harness agent is missing harness_arn" }));
      responseStream.end();
      return;
    }

    const sseHeaders = {
      statusCode: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        "x-accel-buffering": "no",
      },
    };
    responseStream = awslambda.HttpResponseStream.from(responseStream, sseHeaders);

    try {
      const messages = historyToHarnessMessages(body.history, body.prompt || "");
      const stream = await invokeHarness({
        harnessArn,
        sessionId: doubleUuid(),   // MVP session model A: stateless, new id per invoke
        messages,
        region: REGION,
      });
      for await (const chunk of translateHarnessStream(stream)) {
        responseStream.write(chunk);
      }
    } catch (err) {
      console.error("harness invoke error:", err);
      const msg = String(err?.message || err || "harness invoke failed");
      responseStream.write(`data: ${JSON.stringify({ __error: msg })}\n\n`);
    } finally {
      responseStream.end();
    }
    return;
  }

  // Build AgentCore request
  const agentArn = route.type === "meta-agent"
    ? META_AGENT_ARN
    : `arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${route.agentId}`;

  const payload = {
    prompt: body.prompt || "",
    history: body.history || [],
    images: body.images,
    model_id: body.model_id,
  };
  // workspace_id is required by both meta-agent (for workspace-scoped tool calls)
  // and agents (for read_document's caller-workspace allowlist).
  payload.workspace_id = route.wsId;
  payload.caller_id = auth.userId;
  if (route.type === "meta-agent") {
    // `mode` selects which Kiro agent config handles the turn. The skill
    // editor sidebar sends "skill_edit" to swap to the tool-less variant;
    // empty / missing defaults to the full meta-agent. Only a short
    // whitelist is forwarded to keep the payload surface honest.
    if (typeof body.mode === "string" && body.mode.length <= 32) {
      payload.mode = body.mode;
    }
    // `action` triggers short-circuit entrypoint branches (currently
    // `list_models`). Same whitelist approach as `mode`: if the caller
    // sends anything unreasonably large, drop it.
    if (typeof body.action === "string" && body.action.length <= 32) {
      payload.action = body.action;
    }
    // `language` feeds the auto-continue supervisor's locale choice
    // ("Continue." vs "继续。"). Accept 2-8 char strings only — BCP-47
    // tags like "en-US" or "zh-Hans" fit; anything longer is suspect.
    if (typeof body.language === "string" && body.language.length <= 8) {
      payload.language = body.language;
    }

    // Hydrate the per-workspace Kiro API key into the payload. We never
    // put this in the Runtime's environment variables because those are
    // plaintext in the control-plane config; payload is transported via
    // SigV4+TLS only. If the workspace hasn't configured a key yet,
    // short-circuit with 400 — the UI surfaces a banner linking to the
    // settings page so the admin can configure it.
    let kiroKey;
    try {
      kiroKey = await fetchKiroKey(route.wsId);
    } catch (err) {
      console.error("fetchKiroKey error:", err);
      const meta = { statusCode: 500, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: "kiro_key_lookup_failed" }));
      responseStream.end();
      return;
    }
    if (!kiroKey) {
      const meta = { statusCode: 400, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: "kiro_not_configured" }));
      responseStream.end();
      return;
    }
    payload.kiro_api_key = kiroKey;
  }

  const commandInput = {
    agentRuntimeArn: agentArn,
    qualifier: "DEFAULT",
    payload: Buffer.from(JSON.stringify(payload)),
  };
  if (body.session_id) {
    if (typeof body.session_id !== "string" || !ID_PATTERN.test(body.session_id) || body.session_id.length > 128) {
      const meta = { statusCode: 400, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: "Invalid session_id" }));
      responseStream.end();
      return;
    }
    commandInput.runtimeSessionId = body.session_id;
    payload.session_id = body.session_id;
  }

  // Force OTEL span sampling by supplying a W3C traceparent with the
  // `sampled` flag (01). AgentCore seeds its tracer from the inbound
  // context; without this the agent runs with `trace_sampled=False`
  // and zero spans land in aws/spans — the Traces tab would look empty
  // for interactive chats even though the runtime ran fine. EventBridge
  // Scheduler already injects a sampled trace, which is why scheduled
  // runs were visible but chats weren't.
  const rawTrace = genTraceId();
  const rawSpan = genSpanId();
  commandInput.traceId = `Root=1-${rawTrace.slice(0, 8)}-${rawTrace.slice(8)};Parent=${rawSpan};Sampled=1`;
  commandInput.traceParent = `00-${rawTrace}-${rawSpan}-01`;

  // Invoke AgentCore
  let agentResp;
  try {
    agentResp = await agentcore.send(new InvokeAgentRuntimeCommand(commandInput));
  } catch (err) {
    const status = err.$metadata?.httpStatusCode || 500;
    if (status === 424) {
      const meta = { statusCode: 424, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(JSON.stringify({ error: "Agent initializing" }));
      responseStream.end();
      return;
    }
    console.error("invoke_agent_runtime error:", err);
    const meta = { statusCode: 502, headers: { "content-type": "application/json" } };
    responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
    responseStream.write(JSON.stringify({ error: "Failed to invoke agent" }));
    responseStream.end();
    return;
  }

  // Stream SSE response
  const meta = {
    statusCode: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      "x-accel-buffering": "no",
    },
  };
  responseStream = awslambda.HttpResponseStream.from(responseStream, meta);

  // SSE frames can be split across TCP chunks; a single `data:` payload may
  // be tens of KB (tool outputs with base64) so buffer partial lines.
  let sseBuffer = "";
  const flushLines = (flushAll = false) => {
    sseBuffer = sseBuffer.replace(/\r\n/g, "\n");
    const lines = sseBuffer.split("\n");
    sseBuffer = flushAll ? "" : (lines.pop() || "");
    for (const line of lines) {
      if (!line || line.startsWith(":")) continue;
      if (line.startsWith("data: ")) {
        responseStream.write(`${line}\n\n`);
      }
      // Lines that don't start with `data:` are SSE metadata (event:, id:, retry:)
      // or incidental whitespace; drop them rather than synthesizing a new
      // `data:` frame which would corrupt the payload if it's a fragment.
    }
  };

  try {
    const stream = agentResp.response;
    if (stream && Symbol.asyncIterator in stream) {
      for await (const chunk of stream) {
        if (chunk instanceof Uint8Array || Buffer.isBuffer(chunk)) {
          sseBuffer += Buffer.from(chunk).toString("utf-8");
          flushLines();
        }
      }
      flushLines(true);
    } else if (stream && typeof stream.read === "function") {
      // Fallback: read as buffer
      sseBuffer = await new Promise((resolve, reject) => {
        const chunks = [];
        stream.on("data", (c) => chunks.push(c));
        stream.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
        stream.on("error", reject);
      });
      flushLines(true);
    }
  } catch (err) {
    console.error("SSE streaming error:", err);
    responseStream.write(`data: ${JSON.stringify({ error: "Streaming error" })}\n\n`);
  }

  responseStream.end();
});
