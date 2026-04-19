/**
 * Build A2A-compliant AgentCard JSON documents.
 *
 * Shape aligned with a2a-protocol.org spec v0.3.0:
 *   https://a2a-protocol.org/specification#agent-card
 * The canonical top-level fields (name/url/version/protocolVersion/
 * capabilities/securitySchemes/security/skills/defaultInputModes/
 * defaultOutputModes) are what off-the-shelf A2A clients expect.
 *
 * HTTPAuthSecurityScheme with scheme="bearer" + bearerFormat is the
 * IANA-registered HTTP auth pattern referenced by the A2A spec for
 * API-Gateway-in-front deployments.
 */

export function buildPublicAgentCard({
  name,
  runtimeId,
  description,
  version,
  a2aBaseUrl,
  kind = "sub-agent",
}) {
  const skills = kind === "meta-agent"
    ? [
        {
          id: "agent_lifecycle",
          name: "Manage sub-agents",
          description: "Create, update, deploy, archive, and restore sub-agents.",
          tags: ["orchestrator"],
        },
        {
          id: "skill_management",
          name: "Manage skills",
          description: "CRUD reusable skills in the SKILL.md format.",
          tags: ["skills"],
        },
      ]
    : [
        {
          id: "invoke",
          name: "Invoke agent",
          description: description || "Invoke this Agent Studio sub-agent via A2A.",
          tags: ["invoke"],
        },
      ];

  return {
    name,
    description:
      description ||
      `Agent Studio ${kind === "meta-agent" ? "Meta-Agent" : "sub-agent"} ${name}.`,
    url: a2aBaseUrl,
    version: String(version || "1"),
    protocolVersion: "0.3.0",
    preferredTransport: "JSONRPC",
    defaultInputModes: ["text"],
    defaultOutputModes: ["text"],
    capabilities: {
      streaming: true,
      extendedAgentCard: true,
      pushNotifications: false,
      stateTransitionHistory: false,
    },
    securitySchemes: {
      bearerAuth: {
        type: "http",
        scheme: "bearer",
        bearerFormat: "Agent Studio API Key",
        description:
          "Per-user-per-agent API key — generate one in the Agent Studio UI.",
      },
    },
    security: [{ bearerAuth: [] }],
    skills,
    runtimeId,
  };
}

/**
 * Extended AgentCard — returned after Bearer auth. Adds sensitive
 * resource identifiers (runtime ARN) and per-caller context that
 * shouldn't be in the public card.
 */
export function buildExtendedAgentCard(publicCard, { runtimeArn, userId, workspaceId }) {
  return {
    ...publicCard,
    provider: {
      organization: "Agent Studio",
      url: "https://github.com/anthropics/agent-studio",
    },
    runtimeArn,
    caller: { userId, workspaceId },
  };
}
