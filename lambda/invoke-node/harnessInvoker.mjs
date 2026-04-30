/**
 * Invoke an AgentCore Harness and return its event stream.
 *
 * Uses `InvokeHarnessCommand` from @aws-sdk/client-bedrock-agentcore.
 * Phase 0 spike confirmed the command is available in our installed SDK
 * version (^3.1026.0), so no SigV4 fallback is needed.
 */
import {
  BedrockAgentCoreClient,
  InvokeHarnessCommand,
} from "@aws-sdk/client-bedrock-agentcore";

// Cache clients per region (Lambda container reuse).
const clients = new Map();

function getClient(region) {
  let c = clients.get(region);
  if (!c) {
    c = new BedrockAgentCoreClient({ region });
    clients.set(region, c);
  }
  return c;
}

/**
 * @param {{
 *   harnessArn: string,
 *   sessionId: string,    // runtimeSessionId, must be >= 33 chars (see doubleUuid())
 *   messages: Array<{role: string, content: Array<{text: string}>}>,
 *   region: string,
 *   modelId?: string,     // per-invoke model override (chat-page dropdown)
 * }} opts
 * @returns {Promise<AsyncIterable>} native harness event stream
 */
export async function invokeHarness({ harnessArn, sessionId, messages, region, modelId }) {
  const client = getClient(region);
  const cmd = new InvokeHarnessCommand({
    harnessArn,
    runtimeSessionId: sessionId,
    messages,
    ...(modelId && { model: { bedrockModelConfig: { modelId } } }),
  });
  const resp = await client.send(cmd);
  return resp.stream;
}
