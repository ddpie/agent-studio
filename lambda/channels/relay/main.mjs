/**
 * Channel Relay — Entry point.
 *
 * Reads environment variables, loads secrets from Secrets Manager,
 * starts the AdapterManager, and forwards events to the Worker Lambda.
 *
 * Env vars:
 *   WORKSPACE_ID        — workspace this relay serves
 *   WORKER_FUNCTION_NAME — Worker Lambda function name/ARN
 *   AWS_REGION          — AWS region
 */

import { LambdaClient, InvokeCommand } from "@aws-sdk/client-lambda";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, QueryCommand } from "@aws-sdk/lib-dynamodb";
import {
  SecretsManagerClient,
  GetSecretValueCommand,
} from "@aws-sdk/client-secrets-manager";
import { AdapterManager } from "./adapter-manager.mjs";

// ─── Configuration ───────────────────────────────────────────────────────────

const WORKSPACE_ID = process.env.WORKSPACE_ID;
const WORKER_FUNCTION_NAME = process.env.WORKER_FUNCTION_NAME;
const REGION = process.env.AWS_REGION || "us-east-1";
const TABLE_NAME = "agent-studio-channels";

if (!WORKSPACE_ID) {
  console.error("[Relay] WORKSPACE_ID env var is required");
  process.exit(1);
}
if (!WORKER_FUNCTION_NAME) {
  console.error("[Relay] WORKER_FUNCTION_NAME env var is required");
  process.exit(1);
}

// ─── AWS Clients ─────────────────────────────────────────────────────────────

const lambdaClient = new LambdaClient({ region: REGION });
const smClient = new SecretsManagerClient({ region: REGION });
const ddbClient = DynamoDBDocumentClient.from(
  new DynamoDBClient({ region: REGION }),
);

// ─── Secrets Loading ─────────────────────────────────────────────────────────

/**
 * Load all channel configs from DynamoDB and their secrets from Secrets Manager.
 * @returns {Promise<Map<string, { platformConfig: object; secret: object }>>}
 */
async function loadSecrets() {
  // Query all channels for this workspace
  const result = await ddbClient.send(
    new QueryCommand({
      TableName: TABLE_NAME,
      KeyConditionExpression: "workspaceId = :ws",
      ExpressionAttributeValues: { ":ws": WORKSPACE_ID },
    }),
  );

  const channels = (result.Items || []).filter(
    (item) => item.status === "active" || item.status === "provisioning",
  );

  /** @type {Map<string, { platformConfig: object; secret: object }>} */
  const secrets = new Map();

  for (const channel of channels) {
    const channelId = channel.channelId;
    const secretArn = channel.secretArn;

    if (!secretArn) {
      console.warn(`[Relay] No secretArn for channel ${channelId}, skipping`);
      continue;
    }

    try {
      const secretResp = await smClient.send(
        new GetSecretValueCommand({ SecretId: secretArn }),
      );
      const secretValue = JSON.parse(secretResp.SecretString || "{}");

      secrets.set(channelId, {
        platformConfig: channel.platformConfig || {},
        secret: secretValue,
      });
    } catch (err) {
      console.error(
        `[Relay] Failed to load secret for channel ${channelId}:`,
        err,
      );
    }
  }

  return secrets;
}

// ─── Trigger Mode Check ──────────────────────────────────────────────────────

/**
 * Determine if a message should be forwarded based on trigger mode.
 * @param {object} event — InboundEvent from adapter
 * @param {object} channelConfig — channel config from DDB
 * @returns {boolean}
 */
function shouldForward(event, channelConfig) {
  // Always forward non-message events (card_action, bot_event)
  if (event.type !== "message") return true;

  // Always forward private chat messages
  if (event.chatType === "p2p") return true;

  const triggerMode = channelConfig.triggerMode || "mention";

  if (triggerMode === "all") return true;

  if (triggerMode === "mention") {
    // Check if bot was @mentioned in the message
    const mentions = event.mentions || [];
    // Feishu mentions array contains objects with key/id/name;
    // a mention with id.user_id matching the bot's open_id or
    // with key "@_all" is a broadcast. We check if any mention
    // contains the bot identity. Since we don't have the bot's own ID
    // stored here, we rely on the presence of any mention with
    // id_type "app" or simply check if mentions array is non-empty
    // (Feishu only includes @bot mentions in the mentions array
    // for events delivered to that bot).
    return mentions.length > 0;
  }

  if (triggerMode === "keyword") {
    const keywords = channelConfig.triggerKeywords || [];
    const content = (event.content || "").toLowerCase();
    return keywords.some((kw) => content.includes(kw.toLowerCase()));
  }

  return true;
}

// ─── Worker Lambda Invocation ────────────────────────────────────────────────

/**
 * Async-invoke the Worker Lambda with the event payload.
 * Uses InvocationType "Event" for fire-and-forget.
 * @param {object} event — normalized InboundEvent
 */
async function invokeWorker(event) {
  const payload = JSON.stringify(event);

  try {
    await lambdaClient.send(
      new InvokeCommand({
        FunctionName: WORKER_FUNCTION_NAME,
        InvocationType: "Event",
        Payload: new TextEncoder().encode(payload),
      }),
    );
    console.log(
      `[Relay] Invoked worker: type=${event.type}, channelId=${event.channelId}, messageId=${event.messageId || "N/A"}`,
    );
  } catch (err) {
    console.error(`[Relay] Failed to invoke worker:`, err);
  }
}

// ─── Channel Config Cache (for trigger mode checks) ──────────────────────────

/** @type {Map<string, object>} */
let channelConfigCache = new Map();

/**
 * Refresh channel config cache from DynamoDB.
 */
async function refreshConfigCache() {
  try {
    const result = await ddbClient.send(
      new QueryCommand({
        TableName: TABLE_NAME,
        KeyConditionExpression: "workspaceId = :ws",
        ExpressionAttributeValues: { ":ws": WORKSPACE_ID },
      }),
    );
    channelConfigCache = new Map();
    for (const item of result.Items || []) {
      channelConfigCache.set(item.channelId, item);
    }
  } catch (err) {
    console.error(`[Relay] Failed to refresh config cache:`, err);
  }
}

// ─── Main ────────────────────────────────────────────────────────────────────

async function main() {
  console.log(`[Relay] Starting: workspace=${WORKSPACE_ID}, region=${REGION}`);

  // Load secrets
  const secrets = await loadSecrets();
  if (secrets.size === 0) {
    console.warn(`[Relay] No active channels found for workspace ${WORKSPACE_ID}`);
    // Keep process alive — config poll will detect new channels
  }

  // Load initial config cache
  await refreshConfigCache();

  // Start adapter manager
  const manager = new AdapterManager({
    workspaceId: WORKSPACE_ID,
    region: REGION,
    secrets,
  });

  // Handle events from adapters
  manager.onEvent(async (event) => {
    // Check trigger mode
    const channelConfig = channelConfigCache.get(event.channelId) || {};
    if (!shouldForward(event, channelConfig)) {
      console.log(
        `[Relay] Skipped (trigger mode): channelId=${event.channelId}, chatId=${event.chatId}`,
      );
      return;
    }

    // Forward to worker
    await invokeWorker(event);
  });

  await manager.start();

  // Periodically refresh config cache (for trigger mode changes)
  const configCacheInterval = setInterval(refreshConfigCache, 30_000);

  // ─── Graceful Shutdown ───────────────────────────────────────────────────

  async function shutdown(signal) {
    console.log(`[Relay] Received ${signal}, shutting down...`);
    clearInterval(configCacheInterval);
    await manager.stop();
    console.log(`[Relay] Shutdown complete`);
    process.exit(0);
  }

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));

  console.log(`[Relay] Running. Listening for events...`);
}

main().catch((err) => {
  console.error(`[Relay] Fatal error:`, err);
  process.exit(1);
});
