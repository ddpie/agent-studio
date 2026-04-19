import crypto from "node:crypto";
import { GetCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";

export function hashKey(plaintext) {
  return crypto.createHash("sha256").update(plaintext).digest("hex");
}

export function extractBearerToken(authorizationHeader) {
  if (!authorizationHeader || typeof authorizationHeader !== "string") return null;
  const m = authorizationHeader.match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : null;
}

/**
 * Look up a bearer token in agent-studio-a2a-keys. Returns the key row
 * (or null). Does NOT enforce scope — the caller must match keyId /
 * agentId against the request's path parameter.
 *
 * Updates lastUsedAt on successful lookup (fire-and-forget — a write
 * failure never rejects an otherwise-valid auth).
 */
export async function resolveApiKey(ddb, tableName, plaintext) {
  if (!plaintext) return null;
  const h = hashKey(plaintext);
  const resp = await ddb.send(new GetCommand({
    TableName: tableName,
    Key: { apiKeyHash: h },
  }));
  const row = resp.Item;
  if (!row || row.revoked) return null;

  ddb.send(new UpdateCommand({
    TableName: tableName,
    Key: { apiKeyHash: h },
    UpdateExpression: "SET lastUsedAt = :t",
    ExpressionAttributeValues: { ":t": new Date().toISOString() },
  })).catch(() => {});

  return row;
}
