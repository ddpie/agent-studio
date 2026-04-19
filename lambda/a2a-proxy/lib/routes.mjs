/**
 * A2A proxy route parsing.
 *
 * All paths live behind CloudFront behaviour /a2a/*. Sub-agents and the
 * Meta-Agent share the same shape — RFC 8615 well-known card, extended
 * card, and JSON-RPC endpoint — so that generic A2A clients can treat
 * Meta-Agent as a peer of every other agent.
 */
const ID_PATTERN = /^[a-zA-Z0-9_-]+$/;

export function isValidId(s) {
  return typeof s === "string" && s.length > 0 && s.length <= 128 && ID_PATTERN.test(s);
}

export function parseRoute(path) {
  if (!path) return null;

  let m = path.match(/^\/a2a\/agents\/([^/]+)\/\.well-known\/agent-card\.json$/);
  if (m) return isValidId(m[1]) ? { type: "agent-card-public", agentId: m[1] } : null;

  m = path.match(/^\/a2a\/agents\/([^/]+)\/authenticatedExtendedCard$/);
  if (m) return isValidId(m[1]) ? { type: "agent-card-extended", agentId: m[1] } : null;

  m = path.match(/^\/a2a\/agents\/([^/]+)$/);
  if (m) return isValidId(m[1]) ? { type: "agent-rpc", agentId: m[1] } : null;

  if (path === "/a2a/meta-agent/.well-known/agent-card.json")
    return { type: "meta-card-public" };
  if (path === "/a2a/meta-agent/authenticatedExtendedCard")
    return { type: "meta-card-extended" };
  if (path === "/a2a/meta-agent") return { type: "meta-rpc" };

  if (path === "/a2a/health") return { type: "health" };

  return null;
}
