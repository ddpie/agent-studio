/**
 * Router — resolves which agent handles an incoming channel message.
 *
 * Group chat: match chatId in routingRules, else defaultAgentId.
 * Private chat: read user's stored selection, or send selection card.
 */

import { GetCommand, PutCommand, DeleteCommand } from "@aws-sdk/lib-dynamodb";

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

  // Handle /switch command (private chat only)
  if (chatType === "p2p" && content && content.trim() === "/switch") {
    // Clear existing selection
    await clearSelection(ddb, channelId, userId);
    // Send selection card
    const agents = collectAvailableAgents(channelConfig);
    await replier.sendSelectionCard(chatId, agents, accessToken);
    return { action: "switch_sent" };
  }

  // Group chat routing
  if (chatType === "group") {
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
  const selection = await getSelection(ddb, channelId, userId);

  if (selection) {
    // Check if agent still exists in config (stale detection)
    const agents = collectAvailableAgents(channelConfig);
    const agentExists = agents.some((a) => a.agentId === selection.agentId);

    if (!agentExists) {
      // Agent was deleted — clear selection and send new card
      await clearSelection(ddb, channelId, userId);
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
  const agents = collectAvailableAgents(channelConfig);
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
  const { channelId, userId, chatId, action } = message;
  const { agentId } = action;

  // Resolve agent name from config
  const agents = collectAvailableAgents(channelConfig);
  const agent = agents.find((a) => a.agentId === agentId);
  const agentName = agent ? agent.agentName : agentId;

  // Store selection (SK=0, Number type)
  await ddb.send(new PutCommand({
    TableName: HISTORY_TABLE,
    Item: {
      pk: `${channelId}#p2p#${userId}`,
      sk: 0,
      agentId,
      agentName,
      selectedAt: new Date().toISOString(),
    },
  }));

  // Send confirmation message
  const confirmText = `Got it! You're now chatting with ${agentName}. Send me your question!`;
  await replier.sendFallbackMessage(chatId, confirmText, accessToken);

  return { action: "confirmation_sent" };
}

/**
 * Get the user's stored agent selection.
 */
async function getSelection(ddb, channelId, userId) {
  const result = await ddb.send(new GetCommand({
    TableName: HISTORY_TABLE,
    Key: {
      pk: `${channelId}#p2p#${userId}`,
      sk: 0,
    },
  }));
  return result.Item || null;
}

/**
 * Clear the user's stored agent selection.
 */
async function clearSelection(ddb, channelId, userId) {
  await ddb.send(new DeleteCommand({
    TableName: HISTORY_TABLE,
    Key: {
      pk: `${channelId}#p2p#${userId}`,
      sk: 0,
    },
  }));
}

/**
 * Collect all available agents from channel config for the selection card.
 * Sources: defaultAgentId + agents referenced in routingRules.
 */
function collectAvailableAgents(channelConfig) {
  const agentMap = new Map();

  // Default agent always available
  if (channelConfig.defaultAgentId) {
    agentMap.set(channelConfig.defaultAgentId, {
      agentId: channelConfig.defaultAgentId,
      agentName: channelConfig.defaultAgentName || channelConfig.defaultAgentId,
    });
  }

  // Agents from routing rules
  const rules = channelConfig.routingRules || [];
  for (const rule of rules) {
    if (rule.agentId && !agentMap.has(rule.agentId)) {
      agentMap.set(rule.agentId, {
        agentId: rule.agentId,
        agentName: rule.agentName || rule.agentId,
      });
    }
  }

  return Array.from(agentMap.values());
}
