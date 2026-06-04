/**
 * LinkedAgentsSection — UI for wiring one agent up to call another via A2A.
 *
 * Backed by the Meta-Agent `link_agent` / `unlink_agent` tools. We invoke the
 * Meta-Agent through the standard chat streaming flow (same pattern as
 * SecretsSection) rather than exposing a new REST endpoint — the Meta-Agent
 * already has all the DDB + Secrets Manager + AgentCore redeploy
 * permissions it needs, and this keeps the scope free of CDK / Lambda
 * changes. Trade-off: the user sees a non-instant response while the
 * source agent redeploys (~60s). We surface that as inline status.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link2, Link2Off, Loader2, Plus } from "lucide-react";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { fetchAgent } from "../../lib/api-client";
import Section from "./shared/Section";

const inputClass = "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";

export interface LinkedAgentEntry {
  agent_id: string;
  display_name?: string;
  description?: string;
}

interface Props {
  agentId: string;
  linkedAgents: LinkedAgentEntry[];
  onChange: (next: LinkedAgentEntry[]) => void;
}

/**
 * Parse the Meta-Agent stream for tool result markers and decide whether
 * the requested tool (e.g. `link_agent`) succeeded.
 *
 * The Meta-Agent emits JSON markers of shape
 *   {"__tool":"result","name":"link_agent","input":"<b64>","output":"<b64>"}
 * inline with plain text. Same parsing discipline as `chat-store.ts`.
 *
 * Returns:
 *   - { ok: true }  if a result for `toolName` decoded to JSON without an
 *     `error` key.
 *   - { ok: false, error } if a result for `toolName` decoded to JSON with
 *     an `error` key — or if no matching result marker was seen at all
 *     (stream ended without the tool running).
 */
function interpretToolStream(
  raw: string,
  toolName: string,
): { ok: true } | { ok: false; error: string } {
  const markerRe = /\{"__tool":"result"[^}]*\}/g;
  let match: RegExpExecArray | null;
  let sawMatching = false;
  while ((match = markerRe.exec(raw)) !== null) {
    let parsed: { name?: string; output?: string } | null = null;
    try {
      parsed = JSON.parse(match[0]);
    } catch {
      continue;
    }
    if (parsed?.name !== toolName) continue;
    sawMatching = true;
    let decoded = "";
    try {
      decoded = parsed.output
        ? new TextDecoder().decode(Uint8Array.from(atob(parsed.output), (c) => c.charCodeAt(0)))
        : "";
    } catch {
      decoded = parsed.output || "";
    }
    let body: Record<string, unknown> | null = null;
    try {
      body = JSON.parse(decoded);
    } catch {
      // Non-JSON output: the tool returned free text. Treat as success —
      // we have no structured error to surface.
      return { ok: true };
    }
    if (body && typeof body === "object" && "error" in body) {
      const msg = typeof body.error === "string" ? body.error : JSON.stringify(body.error);
      return { ok: false, error: msg };
    }
    return { ok: true };
  }
  if (!sawMatching) {
    // Stream ended without the tool ever producing a result marker.
    // The Meta-Agent likely refused or timed out; surface as an error
    // so we don't falsely mark the agent as linked.
    return { ok: false, error: `Meta-Agent did not execute ${toolName}.` };
  }
  return { ok: true };
}

export default function LinkedAgentsSection({ agentId, linkedAgents, onChange }: Props) {
  const { t } = useTranslation();
  const { agents, fetchAgents } = useAgentListStore();
  const { currentWorkspace } = useWorkspaceStore();
  const role = currentWorkspace?.role || "viewer";
  const canEdit = role === "editor" || role === "admin" || role === "owner";
  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null); // id currently mutating
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!agents.length) fetchAgents();
  }, [agents.length, fetchAgents]);

  const linkedIds = useMemo(() => new Set(linkedAgents.map((l) => l.agent_id)), [linkedAgents]);
  const candidates = useMemo(
    () => agents.filter((a) => a.id !== agentId && !linkedIds.has(a.id) && a.status !== "archived"),
    [agents, agentId, linkedIds],
  );

  const invokeMeta = async (prompt: string): Promise<string> => {
    let result = "";
    const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
    for await (const chunk of stream) result += chunk;
    return result;
  };

  /**
   * Refetch the source agent after a link/unlink round-trip so we reflect
   * the canonical `linked_agents` list from DDB rather than an optimistic
   * guess. Returns the refreshed list, or `null` if the refetch fails.
   */
  const refetchLinkedAgents = async (): Promise<LinkedAgentEntry[] | null> => {
    try {
      const fresh = await fetchAgent(agentId);
      const list = Array.isArray(fresh?.linked_agents) ? fresh.linked_agents : [];
      return list as LinkedAgentEntry[];
    } catch {
      return null;
    }
  };

  const handleAdd = async () => {
    if (!selected) return;
    if (!canEdit) return;
    setBusy(selected);
    setStatus(t("linkedAgents.linking"));
    setError(null);
    try {
      const result = await invokeMeta(
        `Execute link_agent with these parameters:\n- source_agent_id: ${agentId}\n- target_agent_id: ${selected}\n\nDo NOT ask for confirmation. Execute immediately and report back the JSON result.`,
      );
      const outcome = interpretToolStream(result, "link_agent");
      if (!outcome.ok) {
        setError(t("linkedAgents.linkFailed"));
        setStatus(null);
        return;
      }
      // Stream completed cleanly — confirm via authoritative DDB read
      // before flipping UI state to "linked".
      const fresh = await refetchLinkedAgents();
      if (fresh && fresh.some((l) => l.agent_id === selected)) {
        onChange(fresh);
        setStatus(t("linkedAgents.linked"));
        setSelected("");
      } else if (fresh) {
        // Stream said ok but DDB doesn't reflect it — treat as failure.
        setError(t("linkedAgents.linkFailed"));
        setStatus(null);
      } else {
        // Refetch failed; fall back to the optimistic update but only
        // after a clean stream.
        const target = agents.find((a) => a.id === selected);
        onChange([
          ...linkedAgents,
          {
            agent_id: selected,
            display_name: target?.displayName || selected,
            description: target?.description || "",
          },
        ]);
        setStatus(t("linkedAgents.linked"));
        setSelected("");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(null);
    } finally {
      setBusy(null);
    }
  };

  const handleRemove = async (targetId: string) => {
    if (!canEdit) return;
    setBusy(targetId);
    setStatus(t("linkedAgents.unlinking"));
    setError(null);
    try {
      const result = await invokeMeta(
        `Execute unlink_agent with these parameters:\n- source_agent_id: ${agentId}\n- target_agent_id: ${targetId}\n\nDo NOT ask for confirmation. Execute immediately and report back the JSON result.`,
      );
      const outcome = interpretToolStream(result, "unlink_agent");
      if (!outcome.ok) {
        setError(t("linkedAgents.unlinkFailed"));
        setStatus(null);
        return;
      }
      const fresh = await refetchLinkedAgents();
      if (fresh && !fresh.some((l) => l.agent_id === targetId)) {
        onChange(fresh);
        setStatus(t("linkedAgents.unlinked"));
      } else if (fresh) {
        setError(t("linkedAgents.unlinkFailed"));
        setStatus(null);
      } else {
        onChange(linkedAgents.filter((l) => l.agent_id !== targetId));
        setStatus(t("linkedAgents.unlinked"));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus(null);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Section title={t("linkedAgents.title")} icon={<Link2 className="w-3.5 h-3.5" />}>
      <p className="text-[11px] text-gray-400 dark:text-gray-500">{t("linkedAgents.description")}</p>

      {error && (
        <div className="px-3 py-2 rounded-lg text-xs bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
          {error}
        </div>
      )}
      {status && !error && !busy && (
        <div className="px-3 py-2 rounded-lg text-xs bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border border-green-200 dark:border-green-800">
          {status}
        </div>
      )}

      {linkedAgents.length > 0 ? (
        <ul className="space-y-1">
          {linkedAgents.map((link) => (
            <li
              key={link.agent_id}
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-700"
            >
              <Link2 className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-gray-800 dark:text-gray-100 truncate">
                  {link.display_name || link.agent_id}
                </div>
                <div className="text-[11px] text-gray-400 dark:text-gray-500 font-mono truncate">{link.agent_id}</div>
                {link.description && (
                  <div className="text-[11px] text-gray-500 dark:text-gray-400 truncate">{link.description}</div>
                )}
              </div>
              <button
                onClick={() => handleRemove(link.agent_id)}
                disabled={busy === link.agent_id || !canEdit}
                aria-disabled={busy === link.agent_id || !canEdit}
                className="p-1.5 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-gray-300 dark:disabled:hover:text-gray-600"
                title={canEdit ? t("linkedAgents.unlink") : t("linkedAgents.editorRequired")}
              >
                {busy === link.agent_id ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Link2Off className="w-3.5 h-3.5" />
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12px] text-gray-400 dark:text-gray-500 italic">{t("linkedAgents.empty")}</p>
      )}

      <div className="flex gap-2 items-center pt-1">
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className={inputClass + " flex-1 disabled:opacity-50 disabled:cursor-not-allowed"}
          disabled={busy !== null || candidates.length === 0 || !canEdit}
          title={canEdit ? undefined : t("linkedAgents.editorRequired")}
        >
          <option value="">
            {candidates.length === 0
              ? t("linkedAgents.noCandidates")
              : t("linkedAgents.selectAgent")}
          </option>
          {candidates.map((a) => (
            <option key={a.id} value={a.id}>
              {a.displayName} ({a.id})
            </option>
          ))}
        </select>
        <button
          onClick={handleAdd}
          disabled={!selected || busy !== null || !canEdit}
          aria-disabled={!selected || busy !== null || !canEdit}
          title={canEdit ? undefined : t("linkedAgents.editorRequired")}
          className="flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy === selected ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Plus className="w-3.5 h-3.5" />
          )}
          {t("linkedAgents.link")}
        </button>
      </div>
      {!canEdit && (
        <p className="text-[11px] text-gray-400 dark:text-gray-500 italic">
          {t("linkedAgents.editorRequired")}
        </p>
      )}
    </Section>
  );
}
