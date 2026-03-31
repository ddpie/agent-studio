import { create } from "zustand";
import { fetchAuthSession } from "aws-amplify/auth";
import { getCurrentUser } from "aws-amplify/auth";
import { agentConfig } from "../config";

export interface AgentInfo {
  name: string;
  displayName: string;
  id: string;
  status: string;
  description: string;
}

interface AgentListState {
  agents: AgentInfo[];
  loading: boolean;
  fetchAgents: () => Promise<void>;
}

/** Query DynamoDB owner-index to get agent IDs and display names owned by current user */
async function queryOwnedAgents(): Promise<Map<string, string>> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return new Set();

    const { username } = await getCurrentUser();
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

    const body = JSON.stringify({
      TableName: "agent-studio-agents",
      IndexName: "owner-index",
      KeyConditionExpression: "#o = :uid",
      ExpressionAttributeNames: { "#o": "owner" },
      ExpressionAttributeValues: { ":uid": { S: username } },
    });

    const url = new URL(`https://dynamodb.${agentConfig.region}.amazonaws.com`);
    const signed = await signer.sign({
      method: "POST",
      protocol: url.protocol,
      hostname: url.hostname,
      path: "/",
      query: {},
      headers: {
        "Content-Type": "application/x-amz-json-1.0",
        "X-Amz-Target": "DynamoDB_20120810.Query",
        Host: url.host,
      },
      body,
    });

    const response = await fetch(url.toString(), {
      method: "POST",
      headers: signed.headers as Record<string, string>,
      body,
    });

    if (!response.ok) return new Map();
    const data = await response.json();
    const agents = new Map<string, string>();
    for (const item of data.Items || []) {
      if (item.agentId?.S) {
        agents.set(item.agentId.S, item.displayName?.S || "");
      }
    }
    return agents;
  } catch {
    return new Map();
  }
}

async function listAgentRuntimes() {
  const { credentials } = await fetchAuthSession();
  if (!credentials) throw new Error("Not authenticated");

  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");

  const signer = new SignatureV4({
    service: "bedrock-agentcore",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const url = new URL(
    `https://bedrock-agentcore-control.${agentConfig.region}.amazonaws.com/runtimes/`
  );

  const signed = await signer.sign({
    method: "POST" as const,
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: {} as Record<string, string>,
    headers: { "Content-Type": "application/json", Host: url.host },
    body: "",
  });

  const response = await fetch(url.toString(), {
    method: "POST",
    headers: signed.headers as Record<string, string>,
    body: "",
  });

  if (!response.ok) throw new Error(`Control plane error ${response.status}`);
  return response.json();
}

export const useAgentListStore = create<AgentListState>((set) => ({
  agents: [],
  loading: false,

  fetchAgents: async () => {
    set({ loading: true });
    try {
      const [data, ownedAgents] = await Promise.all([
        listAgentRuntimes(),
        queryOwnedAgents(),
      ]);

      const runtimes = data.agentRuntimes || data.agentRuntimeSummaries || [];

      const agents: AgentInfo[] = runtimes
        .filter((rt: Record<string, string>) => {
          if (rt.agentRuntimeName === "agentStudioMeta") return false;
          if (ownedAgents.size > 0) return ownedAgents.has(rt.agentRuntimeId);
          return true;
        })
        .map((rt: Record<string, string>) => ({
          name: rt.agentRuntimeName,
          displayName: ownedAgents.get(rt.agentRuntimeId) || rt.agentRuntimeName,
          id: rt.agentRuntimeId,
          status: rt.status,
          description: rt.description || "",
        }));

      set({ agents, loading: false });
    } catch (err) {
      console.error("Failed to fetch agents:", err);
      set({ agents: [], loading: false });
    }
  },
}));
