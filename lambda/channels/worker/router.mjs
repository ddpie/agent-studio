/**
 * Router — resolves which agent handles an incoming channel message.
 *
 * Group chat: match chatId in routingRules, else defaultAgentId.
 * Private chat: read user's stored selection, or send selection card.
 */

import { GetCommand, PutCommand, DeleteCommand, QueryCommand } from "@aws-sdk/lib-dynamodb";

const HISTORY_TABLE = process.env.HISTORY_TABLE || "agent-studio-channel-history";

/**
 * @typedef {Object} RouteResult
 * @property {"invoke"|"selection_sent"|"switch_sent"|"confirmation_sent"} action
 * @property {string} [agentId] - Present when action is "invoke"
 * @property {string} [agentName] - Present when action is "invoke"
 */

/**
 * Resolve which agent should handle this message.
 *
 * @param {import("@aws-sdk/lib-dynamodb").DynamoDBDocumentClient} ddb
 * @param {object} message - Inbound message payload
 * @param {object} channelConfig - Channel config from DDB (includes routingRules, defaultAgentId)
 * @param {object} replier - FeishuReplier instance
 * @param {string} accessToken - Platform access token
 * @returns {Promise<RouteResult>}
 */
export async function resolveRoute(ddb, message, channelConfig, replier, accessToken) {
  const { chatType, chatId, userId, content, channelId } = message;

  // Handle /switch command (private chat)
  if (chatType === "p2p" && content && content.trim() === "/switch") {
    await clearSelection(ddb, channelId, `p2p#${userId}`);
    const agents = await collectAvailableAgents(channelConfig, ddb);
    await replier.sendSelectionCard(chatId, agents, accessToken);
    return { action: "switch_sent" };
  }

  // Strip @mention placeholders from content (Feishu encodes as @_user_N)
  const userContent = (content || "").replace(/@_user_\d+/g, "").trim();

  // Group chat: /switch or /agents command → send selection card
  if (chatType === "group" && (userContent === "/switch" || userContent === "/agents")) {
    const agents = await collectAvailableAgents(channelConfig, ddb);
    await replier.sendSelectionCard(chatId, agents, accessToken);
    return { action: "selection_sent" };
  }

  // Group chat: empty @mention (no real content) → send selection card
  if (chatType === "group" && !userContent) {
    const agents = await collectAvailableAgents(channelConfig, ddb);
    await replier.sendSelectionCard(chatId, agents, accessToken);
    return { action: "selection_sent" };
  }

  // Group chat routing
  if (chatType === "group") {
    // Check if user has a per-user selection in this group
    const groupSelection = await getSelection(ddb, channelId, `${chatId}#${userId}`);
    if (groupSelection) {
      const agents = await collectAvailableAgents(channelConfig, ddb);
      const agentExists = agents.some((a) => a.agentId === groupSelection.agentId);
      if (agentExists) {
        return { action: "invoke", agentId: groupSelection.agentId, agentName: groupSelection.agentName || groupSelection.agentId };
      }
      await clearSelection(ddb, channelId, `${chatId}#${userId}`);
    }

    // Check routing rules (admin-configured per-group binding)
    const rules = channelConfig.routingRules || [];
    for (const rule of rules) {
      if (rule.type === "group" && rule.chatId === chatId) {
        return { action: "invoke", agentId: rule.agentId, agentName: rule.agentName || rule.agentId };
      }
    }
    // Fallback to default
    return {
      action: "invoke",
      agentId: channelConfig.defaultAgentId,
      agentName: channelConfig.defaultAgentName || channelConfig.defaultAgentId,
    };
  }

  // Private chat routing — check stored selection
  const selection = await getSelection(ddb, channelId, `p2p#${userId}`);

  if (selection) {
    // Check if agent still exists in config (stale detection)
    const agents = await collectAvailableAgents(channelConfig, ddb);
    const agentExists = agents.some((a) => a.agentId === selection.agentId);

    if (!agentExists) {
      await clearSelection(ddb, channelId, `p2p#${userId}`);
      await replier.sendSelectionCard(chatId, agents, accessToken);
      return { action: "selection_sent" };
    }

    return {
      action: "invoke",
      agentId: selection.agentId,
      agentName: selection.agentName || selection.agentId,
    };
  }

  // No selection — send selection card
  const agents = await collectAvailableAgents(channelConfig, ddb);
  await replier.sendSelectionCard(chatId, agents, accessToken);
  return { action: "selection_sent" };
}

/**
 * Handle card_action event — store user's agent selection.
 *
 * @param {import("@aws-sdk/lib-dynamodb").DynamoDBDocumentClient} ddb
 * @param {object} message - card_action payload
 * @param {object} channelConfig - Channel config
 * @param {object} replier - FeishuReplier instance
 * @param {string} accessToken - Platform access token
 * @returns {Promise<RouteResult>}
 */
export async function handleCardAction(ddb, message, channelConfig, replier, accessToken) {
  const { channelId, userId, chatId, chatType, action } = message;
  const { agentId } = action;

  // Resolve agent name from config
  const agents = await collectAvailableAgents(channelConfig, ddb);
  const agent = agents.find((a) => a.agentId === agentId);
  const agentName = agent ? agent.agentName : agentId;

  // Store selection — different key for group vs p2p
  const selectionKey = chatType === "group" ? `${chatId}#${userId}` : `p2p#${userId}`;
  await ddb.send(new PutCommand({
    TableName: HISTORY_TABLE,
    Item: {
      pk: `${channelId}#${selectionKey}`,
      sk: 0,
      agentId,
      agentName,
      selectedAt: new Date().toISOString(),
    },
  }));

  // Send confirmation message
  const lang = channelConfig.language || "zh";
  const confirmText = lang === "zh"
    ? `好的！你现在正在和「${agentName}」对话。直接发消息吧！`
    : `Got it! You're now chatting with ${agentName}. Send me your question!`;
  await replier.sendFallbackMessage(chatId, confirmText, accessToken);

  return { action: "confirmation_sent" };
}

/**
 * Get the user's stored agent selection.
 * @param {string} selectionKey - e.g. "p2p#userId" or "chatId#userId"
 */
async function getSelection(ddb, channelId, selectionKey) {
  const result = await ddb.send(new GetCommand({
    TableName: HISTORY_TABLE,
    Key: {
      pk: `${channelId}#${selectionKey}`,
      sk: 0,
    },
  }));
  return result.Item || null;
}

/**
 * Clear the user's stored agent selection.
 * @param {string} selectionKey - e.g. "p2p#userId" or "chatId#userId"
 */
async function clearSelection(ddb, channelId, selectionKey) {
  await ddb.send(new DeleteCommand({
    TableName: HISTORY_TABLE,
    Key: {
      pk: `${channelId}#${selectionKey}`,
      sk: 0,
    },
  }));
}

const AGENTS_TABLE = process.env.AGENTS_TABLE || "agent-studio-agents";

/**
 * Collect ALL agents in the workspace for the selection card.
 * Queries the agents table by workspace_id (GSI).
 */
async function collectAvailableAgents(channelConfig, ddb) {
  const workspaceId = channelConfig.workspaceId;
  try {
    const resp = await ddb.send(new QueryCommand({
      TableName: AGENTS_TABLE,
      IndexName: "workspace-index",
      KeyConditionExpression: "workspace_id = :ws",
      ExpressionAttributeValues: { ":ws": workspaceId },
      ProjectionExpression: "agentId, #n",
      ExpressionAttributeNames: { "#n": "name" },
    }));
    return (resp.Items || [])
      .filter((item) => item.agentId)
      .map((item) => ({ agentId: item.agentId, agentName: item.name || item.agentId }));
  } catch (err) {
    console.warn("Failed to query workspace agents:", err.message);
    // Fallback: return only what's in channel config
    const agents = [];
    if (channelConfig.defaultAgentId) {
      agents.push({ agentId: channelConfig.defaultAgentId, agentName: channelConfig.defaultAgentId });
    }
    return agents;
  }
}
