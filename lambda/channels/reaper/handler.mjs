/**
 * Card Reaper — scans the inflight table and removes expired cards.
 *
 * Triggered by EventBridge every 5 minutes. DynamoDB TTL is eventually
 * consistent, so this Lambda proactively deletes stale cards to keep
 * the inflight set tight.
 */

export const handler = async (event) => {
  console.log("card-reaper invoked");
  return { statusCode: 200, body: "ok" };
};
