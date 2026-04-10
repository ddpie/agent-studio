import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Loader2 } from "lucide-react";
import { useAgentEditStore } from "../../stores/agent-edit-store";
import type { AgentMetadata } from "../../lib/agent-metadata";

export default function AgentProposalCard({ json }: { json: string }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { openNewWithData } = useAgentEditStore();
  let proposal: Record<string, unknown> | null = null;
  let parseError = "";
  try {
    proposal = JSON.parse(json);
  } catch (e) {
    try {
      const repaired = json.replace(/,\s*}/g, "}").replace(/,\s*]/g, "]");
      proposal = JSON.parse(repaired);
    } catch {
      parseError = e instanceof Error ? e.message : "Invalid JSON";
    }
  }

  if (!proposal) {
    const looksComplete = json.trimEnd().endsWith("}");
    return (
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 p-3 my-2 text-xs not-prose">
        {parseError && looksComplete ? (
          <div>
            <p className="text-red-500 text-[11px] mb-1">{t("chat.parseFailed")}</p>
            <details className="text-[10px] text-gray-500">
              <summary className="cursor-pointer">{t("chat.showRawJson")}</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all bg-gray-100 dark:bg-gray-700 p-2 rounded max-h-40 overflow-y-auto">{json}</pre>
            </details>
          </div>
        ) : (
          <div className="animate-pulse">
            <div className="h-4 bg-gray-200 rounded w-1/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-2/3 mb-2" />
            <div className="h-3 bg-gray-200 rounded w-1/2 mb-2" />
            <div className="text-[11px] text-gray-500 flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> {t("chat.generatingProposal")}
            </div>
          </div>
        )}
      </div>
    );
  }

  const name = String(proposal.agent_name || "");
  const desc = String(proposal.description || "");
  const template = String(proposal.template_id || "");
  const welcome = String(proposal.welcome_message || "");
  const suggestions = String(proposal.suggestions || "").split("|").filter(Boolean);
  const toolNames = String(proposal.tool_names || "").split(",").filter(Boolean);
  const tier = String(proposal.permission_tier || "readonly");
  const supportsImages = Boolean(proposal.supports_images);
  const systemPrompt = String(proposal.system_prompt || "");
  const toolDefs = String(proposal.tool_definitions || "");

  const handleEditAndCreate = () => {
    openNewWithData({
      name,
      display_name: name,
      description: desc,
      template_id: template,
      system_prompt: systemPrompt,
      tool_definitions: toolDefs,
      tool_names: toolNames.join(","),
      welcome_message: welcome,
      suggestions,
      supports_images: supportsImages,
    } as Partial<AgentMetadata>);
    const draftId = useAgentEditStore.getState().agentId;
    if (draftId) navigate(`/agents/edit/${draftId}`);
  };

  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-900/20 p-3 my-2 text-xs not-prose">
      <div className="flex items-center justify-between mb-2">
        <span className="font-semibold text-blue-900 dark:text-blue-100">{name}</span>
        <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 dark:bg-blue-800 text-blue-600 dark:text-blue-300 rounded">{tier}</span>
      </div>
      {desc && <p className="text-gray-600 dark:text-gray-400 mb-2">{desc}</p>}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500 mb-2">
        {template && <div>{t("chat.proposalTemplate")} <span className="text-gray-700 dark:text-gray-300">{template}</span></div>}
        <div>{t("chat.proposalImages")} <span className="text-gray-700 dark:text-gray-300">{supportsImages ? t("chat.yes") : t("chat.no")}</span></div>
        {toolNames.length > 0 && (
          <div className="col-span-2">{t("chat.proposalTools")} {toolNames.map(t2 => (
            <span key={t2} className="inline-block px-1.5 py-0.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[10px] mr-1">{t2.trim()}</span>
          ))}</div>
        )}
      </div>
      {suggestions.length > 0 && (
        <div className="text-[10px] text-gray-400 mb-2">
          {t("chat.proposalSuggestions")} {suggestions.join(" / ")}
        </div>
      )}
      {systemPrompt && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">{t("chat.proposalSystemPrompt")}</summary>
          <pre className="mt-1 p-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded text-[11px] text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-48 overflow-y-auto">{systemPrompt}</pre>
        </details>
      )}
      {toolDefs && (
        <details className="mb-2">
          <summary className="text-[11px] text-gray-500 cursor-pointer select-none">{t("chat.proposalToolDefs")}</summary>
          <pre className="mt-1 p-2 bg-gray-900 dark:bg-gray-950 text-green-300 rounded text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">{toolDefs}</pre>
        </details>
      )}
      <button
        onClick={handleEditAndCreate}
        className="w-full mt-1 px-3 py-1.5 text-xs bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
      >
        {t("chat.editAndCreate")}
      </button>
    </div>
  );
}
