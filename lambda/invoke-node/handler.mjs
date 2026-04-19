/**
 * Invoke Lambda — Node.js streaming proxy for AgentCore Runtime.
 *
 * Uses native RESPONSE_STREAM (streamifyResponse) to proxy SSE from AgentCore.
 * JWT auth + workspace permission + agent ownership checks.
 */
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand } from "@aws-sdk/lib-dynamodb";
import { createRemoteJWKSet, jwtVerify } from "jose";

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
const jwks = createRemoteJWKSet(new URL(JWKS_URL));

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
  if (!item || item.workspace_id !== wsId) return jsonResponse(403, { error: "Forbidden" });
  if (item.status === "archived") return jsonResponse(404, { error: "Agent is archived" });
  return null;
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
  if (route.type === "agent") {
    const ownerErr = await checkAgentOwnership(route.agentId, route.wsId);
    if (ownerErr) {
      const meta = { statusCode: ownerErr.statusCode, headers: { "content-type": "application/json" } };
      responseStream = awslambda.HttpResponseStream.from(responseStream, meta);
      responseStream.write(ownerErr.body);
      responseStream.end();
      return;
    }
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
  // and sub-agents (for read_document's caller-workspace allowlist).
  payload.workspace_id = route.wsId;
  if (route.type === "meta-agent") {
    payload.caller_id = auth.userId;
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
  }

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

  try {
    const stream = agentResp.response;
    if (stream && Symbol.asyncIterator in stream) {
      for await (const chunk of stream) {
        if (chunk instanceof Uint8Array || Buffer.isBuffer(chunk)) {
          const text = Buffer.from(chunk).toString("utf-8");
          for (const line of text.split("\n")) {
            const trimmed = line.trim();
            if (!trimmed || trimmed.startsWith(":")) continue;
            if (trimmed.startsWith("data: ")) {
              responseStream.write(`${trimmed}\n\n`);
            } else {
              responseStream.write(`data: ${trimmed}\n\n`);
            }
          }
        }
      }
    } else if (stream && typeof stream.read === "function") {
      // Fallback: read as buffer
      const raw = await new Promise((resolve, reject) => {
        const chunks = [];
        stream.on("data", (c) => chunks.push(c));
        stream.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
        stream.on("error", reject);
      });
      for (const line of raw.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith(":")) continue;
        if (trimmed.startsWith("data: ")) {
          responseStream.write(`${trimmed}\n\n`);
        } else {
          responseStream.write(`data: ${trimmed}\n\n`);
        }
      }
    }
  } catch (err) {
    console.error("SSE streaming error:", err);
    responseStream.write(`data: ${JSON.stringify({ error: "Streaming error" })}\n\n`);
  }

  responseStream.end();
});
