/**
 * Token Manager — manages Feishu tenant_access_token with DynamoDB cache.
 *
 * Checks DDB cache first; if expired (or expiring within 300s), refreshes
 * from Feishu API and caches the new token.
 */

import { GetCommand, PutCommand } from "@aws-sdk/lib-dynamodb";

const TOKENS_TABLE = process.env.TOKENS_TABLE || "agent-studio-channel-tokens";
const TOKEN_BUFFER_SECONDS = 300; // refresh 5 min before expiry

/**
 * Get a valid platform access token, using DDB cache when possible.
 *
 * @param {import("@aws-sdk/lib-dynamodb").DynamoDBDocumentClient} ddb
 * @param {string} channelId
 * @param {{appId: string, appSecret: string}} credentials
 * @returns {Promise<string>} tenant_access_token
 */
export async function getAccessToken(ddb, channelId, credentials) {
  // Check cache
  const cached = await ddb.send(new GetCommand({
    TableName: TOKENS_TABLE,
    Key: { channelId },
  }));

  const nowSeconds = Math.floor(Date.now() / 1000);

  if (cached.Item && cached.Item.expiresAt > nowSeconds + TOKEN_BUFFER_SECONDS) {
    return cached.Item.accessToken;
  }

  // Refresh from Feishu
  const response = await fetch(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        app_id: credentials.appId,
        app_secret: credentials.appSecret,
      }),
    }
  );

  if (!response.ok) {
    throw new Error(`Feishu token refresh failed: HTTP ${response.status}`);
  }

  const data = await response.json();

  if (data.code !== 0) {
    throw new Error(`Feishu token refresh error: ${data.msg} (code=${data.code})`);
  }

  const accessToken = data.tenant_access_token;
  const expire = data.expire; // seconds until expiry (usually 7200)
  const expiresAt = nowSeconds + expire;

  // Cache in DDB
  await ddb.send(new PutCommand({
    TableName: TOKENS_TABLE,
    Item: {
      channelId,
      accessToken,
      expiresAt,
      refreshedAt: new Date().toISOString(),
    },
  }));

  return accessToken;
}
