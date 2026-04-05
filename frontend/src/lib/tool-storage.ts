/**
 * Tool Library DynamoDB operations — CRUD for tool templates.
 * Follows the same SigV4 pattern as agent-list-store.ts.
 */
import { fetchAuthSession, getCurrentUser } from "aws-amplify/auth";
import { agentConfig } from "../config";

export interface ToolTemplate {
  id: string;        // = @tool function name (PK)
  name: string;
  description: string;
  category: string;
  code: string;
  builtin: boolean;
  owner: string;
  visibility: string;
  created_at: string;
  updated_at: string;
  deleted?: boolean;
  deleted_at?: string;
}

const TABLE = "agent-studio-tools";

async function ddbRequest(target: string, body: Record<string, unknown>): Promise<unknown> {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  const signer = new SignatureV4({
    service: "dynamodb",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const jsonBody = JSON.stringify(body);
  const url = new URL(`https://dynamodb.${agentConfig.region}.amazonaws.com`);

  const signed = await signer.sign({
    method: "POST",
    protocol: url.protocol,
    hostname: url.hostname,
    path: "/",
    query: {},
    headers: {
      "Content-Type": "application/x-amz-json-1.0",
      "X-Amz-Target": `DynamoDB_20120810.${target}`,
      Host: url.host,
    },
    body: jsonBody,
  });

  const response = await fetch(url.toString(), {
    method: "POST",
    headers: signed.headers as Record<string, string>,
    body: jsonBody,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`DynamoDB ${target} failed (${response.status}): ${text}`);
  }
  return response.json();
}

function itemToTool(item: Record<string, Record<string, unknown>>): ToolTemplate {
  return {
    id: (item.toolId?.S as string) || "",
    name: (item.name?.S as string) || "",
    description: (item.description?.S as string) || "",
    category: (item.category?.S as string) || "custom",
    code: (item.code?.S as string) || "",
    builtin: (item.builtin?.BOOL as boolean) || false,
    owner: (item.owner?.S as string) || "",
    visibility: (item.visibility?.S as string) || "shared",
    created_at: (item.created_at?.S as string) || "",
    updated_at: (item.updated_at?.S as string) || "",
    deleted: (item.deleted?.BOOL as boolean) || false,
    deleted_at: (item.deleted_at?.S as string) || "",
  };
}

/** Scan all tools from DynamoDB */
export async function scanAllTools(): Promise<ToolTemplate[]> {
  const tools: ToolTemplate[] = [];
  let lastKey: unknown = undefined;

  do {
    const body: Record<string, unknown> = { TableName: TABLE };
    if (lastKey) body.ExclusiveStartKey = lastKey;

    const data = await ddbRequest("Scan", body) as {
      Items?: Record<string, Record<string, unknown>>[];
      LastEvaluatedKey?: unknown;
    };

    for (const item of data.Items || []) {
      tools.push(itemToTool(item));
    }
    lastKey = data.LastEvaluatedKey;
  } while (lastKey);

  return tools;
}

/** Save (create or update) a tool template */
export async function putToolItem(tool: ToolTemplate): Promise<void> {
  const { username } = await getCurrentUser();
  const now = new Date().toISOString();

  await ddbRequest("PutItem", {
    TableName: TABLE,
    Item: {
      toolId: { S: tool.id },
      name: { S: tool.name },
      description: { S: tool.description },
      category: { S: tool.category || "custom" },
      code: { S: tool.code },
      builtin: { BOOL: false },
      owner: { S: username },
      visibility: { S: tool.visibility || "shared" },
      created_at: { S: tool.created_at || now },
      updated_at: { S: now },
    },
    // Allow: new tool, own tool, or seed tool (takes ownership on save)
    ConditionExpression: "attribute_not_exists(toolId) OR #o = :owner OR #o = :seed",
    ExpressionAttributeNames: { "#o": "owner" },
    ExpressionAttributeValues: {
      ":owner": { S: username },
      ":seed": { S: "__builtin__" },
    },
  });
}

/** Delete a tool (only own tools — seed tools must be edited first to take ownership) */
export async function deleteToolItem(toolId: string): Promise<void> {
  const { username } = await getCurrentUser();

  await ddbRequest("DeleteItem", {
    TableName: TABLE,
    Key: { toolId: { S: toolId } },
    ConditionExpression: "#o = :owner",
    ExpressionAttributeNames: { "#o": "owner" },
    ExpressionAttributeValues: {
      ":owner": { S: username },
    },
  });
}

/** Soft-delete a tool (mark as deleted via PutItem — no UpdateItem permission needed) */
export async function softDeleteToolItem(toolId: string): Promise<void> {
  // Read current item first
  const data = await ddbRequest("GetItem", {
    TableName: TABLE,
    Key: { toolId: { S: toolId } },
  }) as { Item?: Record<string, Record<string, unknown>> };

  if (!data.Item) throw new Error("Tool not found");

  // Write back with deleted flag
  data.Item.deleted = { BOOL: true };
  data.Item.deleted_at = { S: new Date().toISOString() };
  await ddbRequest("PutItem", { TableName: TABLE, Item: data.Item });
}

/** Restore a soft-deleted tool (remove deleted flag via PutItem) */
export async function restoreToolItem(toolId: string): Promise<void> {
  const data = await ddbRequest("GetItem", {
    TableName: TABLE,
    Key: { toolId: { S: toolId } },
  }) as { Item?: Record<string, Record<string, unknown>> };

  if (!data.Item) throw new Error("Tool not found");

  delete data.Item.deleted;
  delete data.Item.deleted_at;
  await ddbRequest("PutItem", { TableName: TABLE, Item: data.Item });
}
