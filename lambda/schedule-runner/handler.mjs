/**
 * Schedule Runner — drains an AgentCore streaming invoke to completion.
 *
 * EventBridge Scheduler invokes this Lambda with the same payload shape
 * previously sent to the Universal Target:
 *   { AgentRuntimeArn, RuntimeSessionId, Payload }
 *
 * The agent's _stream_and_record writes DDB/S3 run records as it
 * streams. This Lambda's only job is to keep the stream alive until the
 * agent finishes.
 */
import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { randomBytes } from "node:crypto";

const REGION = process.env.AWS_REGION || "us-east-1";
const agentcore = new BedrockAgentCoreClient({ region: REGION });

function genTraceId() { return randomBytes(16).toString("hex"); }
function genSpanId() { return randomBytes(8).toString("hex"); }

export const handler = async (event) => {
  const runtimeArn = event.AgentRuntimeArn;
  const sessionId = event.RuntimeSessionId || "";
  const payloadStr = event.Payload || "{}";

  if (!runtimeArn) {
    console.error("Missing AgentRuntimeArn in event");
    return { statusCode: 400, error: "Missing AgentRuntimeArn" };
  }

  console.log("schedule-runner start", { runtimeArn, sessionId: sessionId.slice(0, 60) });
  const startMs = Date.now();

  const cmd = new InvokeAgentRuntimeCommand({
    agentRuntimeArn: runtimeArn,
    payload: payloadStr,
    runtimeSessionId: sessionId,
    traceParent: `00-${genTraceId()}-${genSpanId()}-01`,
    qualifier: "DEFAULT",
  });

  let chunks = 0;
  try {
    const resp = await agentcore.send(cmd);
    const stream = resp.response;

    if (stream && Symbol.asyncIterator in stream) {
      for await (const chunk of stream) {
        chunks++;
      }
    } else if (stream && typeof stream.read === "function") {
      await new Promise((resolve, reject) => {
        stream.on("data", () => { chunks++; });
        stream.on("end", resolve);
        stream.on("error", reject);
      });
    }
  } catch (err) {
    const durSec = ((Date.now() - startMs) / 1000).toFixed(1);
    console.error("schedule-runner error", { runtimeArn, sessionId: sessionId.slice(0, 60), chunks, durSec, error: err.message });
    return { statusCode: 500, error: err.message };
  }

  const durSec = ((Date.now() - startMs) / 1000).toFixed(1);
  console.log("schedule-runner done", { runtimeArn, sessionId: sessionId.slice(0, 60), chunks, durSec });
  return { statusCode: 200, chunks, durationSec: durSec };
};
