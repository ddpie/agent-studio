import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Loader2 } from "lucide-react";
import { jsonrepair } from "jsonrepair";
import { useAgentEditStore } from "../../stores/agent-edit-store";
import type { SkillIndexEntry } from "../../lib/skill-storage";
import { fetchSkills } from "../../lib/api-client";
import { readGlobalSkillFiles } from "../../lib/agent-skill-storage";
import type { AgentMetadata } from "../../lib/agent-metadata";

const PROPOSAL_FIELDS = [
  "system_prompt", "tool_definitions", "tool_names",
  "mcp_targets", "skills", "welcome_message", "suggestions",
  "supports_images", "permission_tier",
];

function escapeUnquotedStrings(raw: string): string {
  let repaired = raw;
  for (let i = 0; i < PROPOSAL_FIELDS.length - 1; i++) {
    const startMarker = `"${PROPOSAL_FIELDS[i]}":"`;
    const endMarker = `","${PROPOSAL_FIELDS[i + 1]}"`;
    const startIdx = repaired.indexOf(startMarker);
    if (startIdx < 0) continue;
    const valueStart = startIdx + startMarker.length;
    const endIdx = repaired.indexOf(endMarker, valueStart);
    if (endIdx < 0) continue;
    const value = repaired.slice(valueStart, endIdx);
    const escaped = value.replace(/(?<!\\)"/g, '\\"');
    if (escaped !== value) {
      repaired = repaired.slice(0, valueStart) + escaped + repaired.slice(endIdx);
    }
  }
  return repaired;
}

function parseProposalJson(raw: string): { data: Record<string, unknown> | null; error: string } {
  try { return { data: JSON.parse(raw), error: "" }; } catch {}
  try { return { data: JSON.parse(jsonrepair(raw)), error: "" }; } catch {}
  try {
    const patched = escapeUnquotedStrings(raw);
    return { data: JSON.parse(patched), error: "" };
  } catch {}
  try {
    const patched = escapeUnquotedStrings(raw);
    return { data: JSON.parse(jsonrepair(patched)), error: "" };
  } catch (e) {
    return { data: null, error: e instanceof Error ? e.message : "Invalid JSON" };
  }
}

export default function AgentProposalCard({ json }: { json: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openNewWithData, addSkill, setPendingSkillFiles, initSkillFiles } = useAgentEditStore();
  const [attaching, setAttaching] = useState(false);
  const { data: proposal, error: parseError } = parseProposalJson(json);

  if (!proposal) {
    const looksComplete = json.trimEnd().endsWith("}");
    return (
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 p-3 my-2 text-xs not-prose">
        {parseError && looksComplete ? (
          <div>
            <p className="text-red-500 dark:text-red-400 text-[11px] mb-1">{t("chat.parseFailed")}</p>
            <details className="text-[10px] text-gray-500 dark:text-gray-400">
              <summary className="cursor-pointer">{t("chat.showRawJson")}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all bg-gray-100 dark:bg-gray-700 p-2 rounded max-h-40 overflow-y-auto">{json}</pre>
            </details>
          </div>
        ) : (
          <div className="animate-pulse">
            <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-1/3 mb-2" />
            <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-2/3 mb-2" />
            <div className="h-3 bg-gray-200 dark:bg-gray-700 rounded w-1/2 mb-2" />
            <div className="text-[11px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> {t("chat.generatingProposal")}
            </div>
          </div>
        )}
      </div>
    );
  }

  const name = String(proposal.agent_name || "");
  const desc = String(proposal.description || "");
  // template_id is retired — see meta-agent/templates/prompt_templates.py.
  // We intentionally ignore any value the Meta-Agent might still emit
  // (old chat sessions or older deploys might still include one) so the
  // proposal preview matches what will actually be deployed. The field
  // is not forwarded into the agent-edit store either.
  const welcome = String(proposal.welcome_message || "");
  const suggestions = String(proposal.suggestions || "").split("|").filter(Boolean);
  const toolNames = String(proposal.tool_names || "").split(",").filter(Boolean);
  const tier = String(proposal.permission_tier || "readonly");
  const supportsImages = Boolean(proposal.supports_images);
  const systemPrompt = String(proposal.system_prompt || "");
  const toolDefs = String(proposal.tool_definitions || "");
  const mcpTargets = Array.isArray(proposal.mcp_targets)
    ? proposal.mcp_targets as string[]
    : typeof proposal.mcp_targets === "string" && proposal.mcp_targets
      ? (proposal.mcp_targets).split(",").map(s => s.trim()).filter(Boolean)
      : [];
  const skillNames = Array.isArray(proposal.skills)
    ? (proposal.skills as unknown[]).map(s => String(s).trim()).filter(Boolean)
    : typeof proposal.skills === "string" && proposal.skills
      ? (proposal.skills).split(",").map(s => s.trim()).filter(Boolean)
      : [];

  const handleEditAndCreate = async () => {
    if (attaching) return;
    const data = {
      name,
      display_name: name,
      description: desc,
      system_prompt: systemPrompt,
      tool_definitions: toolDefs,
      tool_names: toolNames.join(","),
      mcp_targets: mcpTargets,
      welcome_message: welcome,
      suggestions,
      supports_images: supportsImages,
      permission_tier: tier,
    } as Partial<AgentMetadata>;
    openNewWithData(data);
    const draftId = useAgentEditStore.getState().agentId;

    // Attach proposed skills from the current workspace library. This matches
    // the proposal spec in meta-agent.md: `skills` is a list of workspace
    // library skill `name` values, resolved here against listSkills().
    // Unknown names are silently skipped — the Meta-Agent prompt forbids
    // inventing names, but we still don't want one stale suggestion to
    // block the rest of the flow.
    if (skillNames.length > 0) {
      setAttaching(true);
      try {
        // listSkills() returns the first 100 skills ordered by GSI sort key
        // (newest-first). Workspaces accumulate e2e/test-seed skills over
        // time, so a legitimate library skill can easily fall past the
        // first page. Fetch every page until the requested names are all
        // resolved or pagination runs out.
        const byName = new Map<string, SkillIndexEntry>();
        const want = new Set(skillNames);
        let cursor: string | undefined;
        for (let page = 0; page < 20; page += 1) {
          const resp = await fetchSkills(cursor, 100);
          for (const raw of resp.items || []) {
            const item = raw as Record<string, unknown>;
            const skillId = String(item.skillId ?? "");
            const name = String(item.name ?? "");
            if (!skillId || !name) continue;
            byName.set(name, {
              id: skillId,
              name,
              description: String(item.description ?? ""),
              contentHash: "",
              files: [],
            });
            want.delete(name);
          }
          const nextCursor = (resp as { nextCursor?: string }).nextCursor;
          if (want.size === 0 || !nextCursor) break;
          cursor = nextCursor;
        }

        for (const skillName of skillNames) {
          const globalSkill = byName.get(skillName);
          if (!globalSkill) {
            console.warn(`Skill "${skillName}" not found in workspace library`);
            continue;
          }
          try {
            const { entry, files } = await readGlobalSkillFiles(globalSkill);
            addSkill(entry);
            initSkillFiles(entry.id, {});
            setPendingSkillFiles(entry.id, files);
          } catch (err) {
            console.warn(`Failed to attach skill "${skillName}":`, err);
          }
        }
      } catch (err) {
        console.warn("Failed to load workspace skills for proposal:", err);
      } finally {
        setAttaching(false);
      }
    }

    if (draftId) navigate(`/agents/edit/${draftId}`);
  };

  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/20 p-3 my-2 text-xs not-prose">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-blue-900 dark:text-blue-100">{name}</span>
      </div>
      {desc && <p className="text-gray-600 dark:text-gray-400 mb-2">{desc}</p>}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400 mb-2">
        <div>{t("chat.proposalImages")} <span className="text-gray-700 dark:text-gray-300">{supportsImages ? t("chat.yes") : t("chat.no")}</span></div>
        {toolNames.length > 0 && (
          <div className="col-span-2">{t("chat.proposalTools")} {toolNames.map(t2 => (
            <span key={t2} className="inline-block px-1.5 py-0.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[10px] mr-1">{t2.trim()}</span>
          ))}</div>
        )}
        {mcpTargets.length > 0 && (
          <div className="col-span-2">MCP {mcpTargets.map(t2 => (
            <span key={t2} className="inline-block px-1.5 py-0.5 bg-purple-50 dark:bg-purple-900/30 border border-purple-200 dark:border-purple-700 text-purple-700 dark:text-purple-300 rounded text-[10px] mr-1">{t2}</span>
          ))}</div>
        )}
        {skillNames.length > 0 && (
          <div className="col-span-2">{t("chat.proposalSkills")} {skillNames.map(s => (
            <span key={s} className="inline-block px-1.5 py-0.5 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700 text-emerald-700 dark:text-emerald-300 rounded text-[10px] mr-1">{s}</span>
          ))}</div>
        )}
      </div>
      {suggestions.length > 0 && (
        <div className="text-[10px] text-gray-400 dark:text-gray-500 mb-2">
          {t("chat.proposalSuggestions")} {suggestions.join(" / ")}
        </div>
      )}
      {systemPrompt && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 dark:text-gray-400 cursor-pointer select-none">{t("chat.proposalSystemPrompt")}</summary>
          <pre className="mt-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[11px] text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-48 overflow-y-auto">{systemPrompt}</pre>
        </details>
      )}
      {toolDefs && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 dark:text-gray-400 cursor-pointer select-none">{t("chat.proposalToolDefs")}</summary>
          <pre className="mt-1 p-2 bg-gray-900 dark:bg-gray-950 text-green-300 rounded text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">{toolDefs}</pre>
        </details>
      )}
      <button
        onClick={handleEditAndCreate}
        disabled={attaching}
        className="w-full mt-1 px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors disabled:opacity-70 flex items-center justify-center gap-1.5"
      >
        {attaching && <Loader2 className="w-3 h-3 animate-spin" />}
        {t("chat.editAndCreate")}
      </button>
    </div>
  );
}
