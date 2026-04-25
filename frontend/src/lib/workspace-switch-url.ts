/**
 * On workspace switch, demote detail-view URLs to their list-view ancestor.
 *
 * Why: workspace-scoped resources (agent ids, skill ids, tool ids, chat session ids)
 * don't exist in the target workspace, so landing on `/agents/edit/<id>` would 404.
 * Truncate to the nearest list route that is valid in any workspace.
 *
 * Cross-workspace routes (/marketplace, /settings, /costs, /mcp, /mcp-policy,
 * and list pages) are preserved as-is.
 */

type Rewrite = (segments: string[]) => string[];

const REWRITES: Record<string, Rewrite> = {
  // /agents/* → /agents (chat/edit/detail/runs all carry workspace-scoped ids)
  agents: (segs) => (segs.length > 1 ? ["agents"] : segs),
  // /skills/<id> → /skills; /skills stays
  skills: (segs) => (segs.length > 1 ? ["skills"] : segs),
  // /tools/<id> → /tools; /tools stays
  tools: (segs) => (segs.length > 1 ? ["tools"] : segs),
};

export function demoteHashForWorkspaceSwitch(hash: string): string {
  // hash looks like "#/agents/edit/abc" or "" or "#/"
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart, queryPart] = raw.split("?");
  const clean = pathPart.replace(/^\/+/, "").replace(/\/+$/, "");
  if (!clean) return "";

  const segments = clean.split("/");
  const head = segments[0];
  const rewritten = REWRITES[head] ? REWRITES[head](segments) : segments;

  const path = "/" + rewritten.join("/");
  return queryPart ? `#${path}?${queryPart}` : `#${path}`;
}
