/**
 * Channel Worker Lambda — receives messages from the relay, routes them to
 * the correct Agent, invokes AgentCore, and streams the reply back via
 * Feishu CardKit.
 *
 * Env vars: CHANNELS_TABLE, TOKENS_TABLE, HISTORY_TABLE, INFLIGHT_TABLE,
 *           REGION, ACCOUNT_ID
 */

import { BedrockAgentCoreClient } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, PutCommand, DeleteCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";

import { resolveRoute, handleCardAction } from "./router.mjs";
import { getAccessToken } from "./token-manager.mjs";
import { FeishuReplier } from "./feishu-replier.mjs";
import { streamToReplier, buildSessionId } from "./stream-bridge.mjs";
import { buildHistoryPk, loadHistory, persistMessages } from "./history.mjs";

// --- Configuration ---
const REGION = process.env.REGION || process.env.AWS_REGION || "us-east-1";
const ACCOUNT_ID = process.env.ACCOUNT_ID || "";
const CHANNELS_TABLE = process.env.CHANNELS_TABLE || "agent-studio-channels";
const INFLIGHT_TABLE = process.env.INFLIGHT_TABLE || "agent-studio-channel-inflight";

// --- AWS Clients (lazy-init) ---
const dynamoRaw = new DynamoDBClient({ region: REGION });
const ddb = DynamoDBDocumentClient.from(dynamoRaw);
const secretsClient = new SecretsManagerClient({ region: REGION });
const agentcore = new BedrockAgentCoreClient({ region: REGION });
const replier = new FeishuReplier();

/**
 * Lambda handler — entry point invoked by the relay (async invocation).
 */
export async function handler(event) {
  const startTime = Date.now();

  try {
    const message = typeof event === "string" ? JSON.parse(event) : event;
    const { type, channelId, workspaceId } = message;

    console.log(JSON.stringify({
      action: "worker_start",
      type,
      channelId,
      workspaceId,
      chatId: message.chatId,
      userId: message.userId,
    }));

    // --- Phase 1: Route ---

    // Handle card_action (user clicked agent selection button)
    if (type === "card_action") {
      console.log("card_action payload:", JSON.stringify(message.action));
      try {
        const config = await loadChannelConfig(workspaceId, channelId);
        const accessToken = await getToken(channelId, config);
        await handleCardAction(ddb, message, config, replier, accessToken);
      } catch (err) {
        console.error("card_action error:", err.message, err.stack);
      }
      return { statusCode: 200, body: "card_action_handled" };
    }

    // Handle bot_event (bot added/removed from group)
    if (type === "bot_event") {
      await handleBotEvent(message);
      return { statusCode: 200, body: "bot_event_handled" };
    }

    // Validate message
    if (type !== "message") {
      console.warn(`Unknown event type: ${type}`);
      return { statusCode: 400, body: "unknown_type" };
    }

    if (!message.content || message.content.length > 4000) {
      console.warn("Message validation failed: empty or too long");
      return { statusCode: 400, body: "invalid_content" };
    }

    // Dedup check (inflight table)
    if (message.messageId) {
      const isDup = await checkDedup(channelId, message.messageId);
      if (isDup) {
        console.log("Duplicate message, skipping");
        return { statusCode: 200, body: "dedup_skipped" };
      }
    }

    // Load channel config
    const config = await loadChannelConfig(workspaceId, channelId);
    if (!config) {
      console.error(`Channel config not found: ${workspaceId}/${channelId}`);
      return { statusCode: 404, body: "channel_not_found" };
    }

    // Get access token
    const accessToken = await getToken(channelId, config);

    // Route — resolve which agent handles this message
    const route = await resolveRoute(ddb, message, config, replier, accessToken);

    if (route.action !== "invoke") {
      // Selection card was sent or switch handled — done
      console.log(JSON.stringify({ action: "route_result", result: route.action }));
      return { statusCode: 200, body: route.action };
    }

    const { agentId, agentName } = route;

    // --- Phase 2: Invoke Agent + Stream Reply ---

    // Load conversation history
    const historyPk = buildHistoryPk({
      channelId,
      chatId: message.chatId,
      threadId: message.threadId,
      userId: message.userId,
      agentId,
    });
    const maxTurns = config.maxHistoryTurns || 10;
    const history = await loadHistory(ddb, historyPk, maxTurns);

    // Build agent request payload
    const agentPayload = {
      prompt: message.content,
      history,
      workspace_id: workspaceId,
      caller_id: `channel:${channelId}:${message.userId}`,
    };

    // Build agent ARN
    const agentArn = `arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${agentId}`;

    // Build session ID
    const sessionId = buildSessionId({
      channelId,
      chatId: message.chatId,
      userId: message.userId,
      agentId,
    });

    // Add THINKING reaction to the user's message
    let reactionId = null;
    if (message.messageId) {
      reactionId = await replier.addReaction(message.messageId, "THINKING", accessToken);
    }

    // Create streaming card
    let ctx;
    let useCardKit = true;
    try {
      ctx = await replier.createStreamingReply(message.chatId, accessToken, {
        agentName,
        isPrivateChat: message.chatType === "p2p",
      });
      await replier.writeThinking(ctx);

      // Record in inflight table for Card Reaper
      await recordInflight(ctx.cardId, channelId, message.chatId, config.channelType || "feishu");
    } catch (err) {
      console.warn("CardKit creation failed, will use fallback:", err.message);
      useCardKit = false;
    }

    // Invoke AgentCore and stream
    let responseText = "";
    try {
      if (useCardKit) {
        responseText = await streamToReplier({
          agentcore,
          agentArn,
          payload: agentPayload,
          sessionId,
          replier,
          ctx,
        });

        // Finalize card
        await replier.finalizeReply(ctx);
      } else {
        // Fallback: invoke and collect full response, send as plain text
        responseText = await collectFullResponse(agentArn, agentPayload, sessionId);
        await replier.sendFallbackMessage(message.chatId, responseText, accessToken);
      }
    } catch (err) {
      console.error("Agent invocation error:", err.message);
      if (useCardKit && ctx) {
        await replier.finalizeWithError(ctx, "Sorry, an error occurred while processing your request.");
      } else {
        await replier.sendFallbackMessage(
          message.chatId,
          "Sorry, an error occurred while processing your request.",
          accessToken
        );
      }
      await updateChannelError(workspaceId, channelId, err.message);
      return { statusCode: 500, body: "agent_error" };
    } finally {
      // Remove THINKING reaction
      if (reactionId && message.messageId) {
        await replier.removeReaction(message.messageId, reactionId, accessToken);
      }
      // Remove from inflight
      if (useCardKit && ctx) {
        await removeInflight(ctx.cardId);
      }
    }

    // Persist conversation history
    const ttlDays = config.historyTtlDays || 7;
    await persistMessages(
      ddb,
      historyPk,
      { content: message.content, userName: message.userName, messageId: message.messageId },
      { content: responseText },
      ttlDays
    );

    // Update channel stats
    await updateChannelStats(workspaceId, channelId);

    const duration = Date.now() - startTime;
    console.log(JSON.stringify({
      action: "worker_complete",
      channelId,
      agentId,
      userId: message.userId,
      duration,
      responseLength: responseText.length,
    }));

    return { statusCode: 200, body: "ok" };
  } catch (err) {
    console.error("Worker unhandled error:", err);
    return { statusCode: 500, body: err.message };
  }
}

// --- Internal helpers ---

/**
 * Load channel config from DynamoDB.
 */
async function loadChannelConfig(workspaceId, channelId) {
  const result = await ddb.send(new GetCommand({
    TableName: CHANNELS_TABLE,
    Key: { workspaceId, sk: channelId },
  }));
  return result.Item || null;
}

/**
 * Get platform access token (with caching).
 */
async function getToken(channelId, config) {
  // Load secret
  const secretArn = config.secretArn;
  const secretResp = await secretsClient.send(new GetSecretValueCommand({
    SecretId: secretArn,
  }));
  const secret = JSON.parse(secretResp.SecretString);

  const credentials = {
    appId: config.platformConfig?.feishu?.appId || config.platformConfig?.appId,
    appSecret: secret.appSecret,
  };

  return getAccessToken(ddb, channelId, credentials);
}

/**
 * Check dedup (and record if new).
 */
async function checkDedup(channelId, messageId) {
  const key = `${channelId}#${messageId}`;
  const ttl = Math.floor(Date.now() / 1000) + 300; // 5 min TTL

  try {
    await ddb.send(new PutCommand({
      TableName: INFLIGHT_TABLE,
      Item: { cardId: key, startedAt: Date.now(), ttl },
      ConditionExpression: "attribute_not_exists(cardId)",
    }));
    return false; // Not a duplicate
  } catch (err) {
    if (err.name === "ConditionalCheckFailedException") {
      return true; // Duplicate
    }
    throw err;
  }
}

/**
 * Handle bot added/removed events.
 */
async function handleBotEvent(message) {
  const { channelId, workspaceId, event, chatId, chatName } = message;

  if (event === "bot_added") {
    // Store group metadata
    await ddb.send(new PutCommand({
      TableName: CHANNELS_TABLE,
      Item: {
        workspaceId,
        sk: `${channelId}#group#${chatId}`,
        chatName: chatName || "",
        addedAt: new Date().toISOString(),
      },
    }));
  } else if (event === "bot_removed") {
    // Remove group metadata
    await ddb.send(new DeleteCommand({
      TableName: CHANNELS_TABLE,
      Key: { workspaceId, sk: `${channelId}#group#${chatId}` },
    }));
  }
}

/**
 * Record inflight card for the Card Reaper.
 */
async function recordInflight(cardId, channelId, chatId, platform) {
  await ddb.send(new PutCommand({
    TableName: INFLIGHT_TABLE,
    Item: {
      cardId,
      channelId,
      chatId,
      startedAt: Date.now(),
      platform,
      ttl: Math.floor(Date.now() / 1000) + 1200, // 20 min auto-expire
    },
  }));
}

/**
 * Remove inflight record after card is finalized.
 */
async function removeInflight(cardId) {
  try {
    await ddb.send(new DeleteCommand({
      TableName: INFLIGHT_TABLE,
      Key: { cardId },
    }));
  } catch (err) {
    console.warn("Failed to remove inflight record:", err.message);
  }
}

/**
 * Update channel stats on successful message processing.
 */
async function updateChannelStats(workspaceId, channelId) {
  try {
    await ddb.send(new UpdateCommand({
      TableName: CHANNELS_TABLE,
      Key: { workspaceId, sk: channelId },
      UpdateExpression: "SET lastMessageAt = :now, messageCount = if_not_exists(messageCount, :zero) + :one, errorCount = :zero",
      ExpressionAttributeValues: {
        ":now": new Date().toISOString(),
        ":one": 1,
        ":zero": 0,
      },
    }));
  } catch (err) {
    console.warn("Failed to update channel stats:", err.message);
  }
}

/**
 * Update channel error state.
 */
async function updateChannelError(workspaceId, channelId, errorMsg) {
  try {
    await ddb.send(new UpdateCommand({
      TableName: CHANNELS_TABLE,
      Key: { workspaceId, sk: channelId },
      UpdateExpression: "SET lastError = :err, errorCount = if_not_exists(errorCount, :zero) + :one",
      ExpressionAttributeValues: {
        ":err": errorMsg.slice(0, 500),
        ":one": 1,
        ":zero": 0,
      },
    }));
  } catch (err) {
    console.warn("Failed to update channel error:", err.message);
  }
}

/**
 * Fallback: invoke agent and collect full response without streaming card.
 */
async function collectFullResponse(agentArn, payload, sessionId) {
  const { InvokeAgentRuntimeCommand } = await import("@aws-sdk/client-bedrock-agentcore");

  const agentResp = await agentcore.send(new InvokeAgentRuntimeCommand({
    agentRuntimeArn: agentArn,
    qualifier: "DEFAULT",
    payload: Buffer.from(JSON.stringify(payload)),
    runtimeSessionId: sessionId,
  }));

  // Collect all text from stream
  const stream = agentResp.response;
  if (!stream) return "";

  let text = "";
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
        const data = line.startsWith("data: ") ? line.slice(6).trim() : line;
        if (data.startsWith("{")) {
          try {
            const obj = JSON.parse(data);
            if (obj.__tool || obj.__keepalive) continue;
          } catch { /* treat as text */ }
          text += data;
        } else if (data.startsWith("\"") && data.endsWith("\"")) {
          try {
            const decoded = JSON.parse(data);
            if (typeof decoded === "string") { text += decoded; continue; }
          } catch { /* fall through */ }
          text += data;
        } else if (!data.startsWith(":")) {
          text += data;
        }
      }
    }
  }

  return text;
}
