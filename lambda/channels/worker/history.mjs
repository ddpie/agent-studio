/**
 * Conversation history — read/write message turns for channel conversations.
 *
 * PK scoping rule: if threadId is non-null, use it instead of chatId.
 * This ensures threaded conversations have independent context.
 */

import { QueryCommand, BatchWriteCommand } from "@aws-sdk/lib-dynamodb";

const HISTORY_TABLE = process.env.HISTORY_TABLE || "agent-studio-channel-history";

/**
 * Build the partition key for history queries.
 *
 * Format: channelId#chatId#userId#agentId
 * (or channelId#threadId#userId#agentId when threadId is present)
 */
export function buildHistoryPk({ channelId, chatId, threadId, userId, agentId }) {
  const scope = threadId || chatId;
  return `${channelId}#${scope}#${userId}#${agentId}`;
}

/**
 * Load recent conversation history (most recent turns first, then reversed
 * to chronological order for the agent).
 *
 * @param {import("@aws-sdk/lib-dynamodb").DynamoDBDocumentClient} ddb
 * @param {string} pk - Partition key from buildHistoryPk
 * @param {number} maxTurns - Number of user/assistant turn pairs to load
 * @returns {Promise<Array<{role: string, content: string}>>}
 */
export async function loadHistory(ddb, pk, maxTurns = 10) {
  const result = await ddb.send(new QueryCommand({
    TableName: HISTORY_TABLE,
    KeyConditionExpression: "pk = :pk AND sk > :zero",
    ExpressionAttributeValues: {
      ":pk": pk,
      ":zero": 0,
    },
    ScanIndexForward: false,
    Limit: maxTurns * 2,
  }));

  const items = result.Items || [];
  // Items come newest-first; reverse to chronological
  items.reverse();
  return items.map((item) => ({
    role: item.role,
    content: item.content,
  }));
}

/**
 * Persist a user message and assistant response to history.
 *
 * @param {import("@aws-sdk/lib-dynamodb").DynamoDBDocumentClient} ddb
 * @param {string} pk - Partition key from buildHistoryPk
 * @param {{role: string, content: string, userName?: string, messageId?: string}} userMsg
 * @param {{role: string, content: string}} assistantMsg
 * @param {number} ttlDays - TTL in days for auto-expiry
 */
export async function persistMessages(ddb, pk, userMsg, assistantMsg, ttlDays = 7) {
  const now = Date.now();
  const ttl = Math.floor(now / 1000) + ttlDays * 86400;

  const items = [
    {
      PutRequest: {
        Item: {
          pk,
          sk: now,
          role: "user",
          content: userMsg.content,
          userName: userMsg.userName || undefined,
          messageId: userMsg.messageId || undefined,
          ttl,
        },
      },
    },
    {
      PutRequest: {
        Item: {
          pk,
          sk: now + 1, // +1ms to guarantee ordering
          role: "assistant",
          content: assistantMsg.content,
          ttl,
        },
      },
    },
  ];

  await ddb.send(new BatchWriteCommand({
    RequestItems: {
      [HISTORY_TABLE]: items,
    },
  }));
}
