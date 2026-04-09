import { useEffect, useState, useCallback } from "react";
import { useNavigate, useParams, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useAgentEditStore } from "../../stores/agent-edit-store";
import { Bot, RefreshCw, Loader2, MessageSquare, Settings2, Archive, ChevronDown, RotateCcw, Trash2, Copy } from "lucide-react";
import ConfirmDialog from "../ui/ConfirmDialog";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { fetchAgentSkillFiles, fetchAgentSkillFile } from "../../lib/api-client";
import { fetchAgentMetadata } from "../../lib/agent-metadata";

export default function AgentList({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation();
  const { agents, archivedAgents, loading, fetchAgents } = useAgentListStore();
  const navigate = useNavigate();
  const { agentId } = useParams();
  const location = useLocation();
  const isEditing = location.pathname.includes("/edit/");
  const [pendingAction, setPendingAction] = useState<(() => void) | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [actionLoading, setActionLoading] = useState<{ id: string; action: string } | null>(null);
  const [confirmAction, setConfirmAction] = useState<{ agentId: string; agentName: string; type: "archive" | "restore" | "purge" } | null>(null);

  const executeAgentAction = useCallback(async (agentId: string, type: "archive" | "restore" | "purge") => {
    setActionLoading({ id: agentId, action: type });
    setConfirmAction(null);
    try {
      const cmdMap = { archive: "delete_agent", restore: "restore_agent", purge: "purge_agent" };
      const prompt = `Execute ${cmdMap[type]} with agent_id: ${agentId}. Do NOT ask for confirmation.`;
      let result = "";
      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
      for await (const chunk of stream) result += chunk;
      await fetchAgents();
    } catch (err) {
      console.error(`${type} failed:`, err);
    } finally {
      setActionLoading(null);
    }
  }, [fetchAgents]);

  const handleDuplicate = useCallback(async (agentId: string) => {
    setActionLoading({ id: agentId, action: "duplicate" });    try {
      const agent = await fetchAgentMetadata(agentId);
      if (!agent) throw new Error("Agent not found");

      // Build old-to-new skill ID map
      const skillIdMap = new Map<string, string>();
      const newSkills = (agent.skills || []).map((s: any) => {
        const newId = crypto.randomUUID().slice(0, 8);
        skillIdMap.set(s.id, newId);
        return { ...s, id: newId };
      });

      const data: Record<string, any> = {
        name: (agent.name || "") + "-copy",
        display_name: (agent.display_name || "") + " (Copy)",
        description: agent.description || "",
        system_prompt: agent.system_prompt || "",
        tool_definitions: agent.tool_definitions || "",
        tool_names: agent.tool_names || "",
        model_id: agent.model_id || "",
        default_model_id: agent.default_model_id || "",
        template_id: agent.template_id || "",
        supports_images: agent.supports_images || false,
        welcome_message: agent.welcome_message || "",
        suggestions: agent.suggestions || [],
        skills: newSkills,
      };
      const { openNewWithData, setPendingSkillFiles, initSkillFiles } = useAgentEditStore.getState();

      // Copy skill files before opening draft
      const skillFileMap: Record<string, Record<string, string>> = {};
      await Promise.all((agent.skills || []).map(async (skill: any) => {
        const newId = skillIdMap.get(skill.id);
        if (!newId) return;
        const files = await fetchAgentSkillFiles(agentId, skill.id);
        const contents = await Promise.all(files.map(f => fetchAgentSkillFile(agentId, skill.id, f).then(c => [f, c] as const)));
        const fileContents: Record<string, string> = {};
        for (const [f, c] of contents) {
          if (c !== null) fileContents[f] = c;
        }
        if (Object.keys(fileContents).length > 0) {
          skillFileMap[newId] = fileContents;
        }
      }));

      openNewWithData(data);
      for (const [skillId, files] of Object.entries(skillFileMap)) {
        initSkillFiles(skillId, files);
        setPendingSkillFiles(skillId, files);
      }

      const draftId = useAgentEditStore.getState().agentId;
      if (draftId) navigate(`/agents/edit/${draftId}`);
    } catch (err) {
      console.error("duplicate failed:", err);
    } finally {
      setActionLoading(null);
    }
  }, [navigate]);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const handleSwitch = useCallback((path: string) => {
    if (isEditing && useAgentEditStore.getState().hasChanges()) {
      setPendingAction(() => () => navigate(path));
    } else {
      navigate(path);
    }
  }, [isEditing, navigate]);

  if (collapsed) {
    return (
      <div className="flex flex-col items-center h-full py-3 gap-2">
        {/* Meta-Agent icon */}
        <button
          onClick={() => handleSwitch("/agents")}
          className={`w-9 h-9 rounded-lg flex items-center justify-center transition-colors ${
            !agentId && !isEditing
              ? "bg-blue-100 text-blue-600"
              : "text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 hover:text-gray-600"
          }`}
          title={t("agents.metaAgent")}
        >
          <MessageSquare className="w-4 h-4" />
        </button>

        {agents.length > 0 && <div className="w-5 border-t border-gray-300" />}

        {agents.map((agent) => (
          <div key={agent.id} className="relative group flex flex-col items-center">
            <div className="relative">
              <button
                onClick={() => handleSwitch(`/agents/chat/${agent.id}`)}
                className={`w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold transition-colors ${
                  agentId === agent.id
                    ? "bg-blue-100 text-blue-600"
                    : "text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700 hover:text-gray-600"
                }`}
                title={agent.displayName}
              >
                {agent.displayName.charAt(0).toUpperCase()}
              </button>
              <span className={`absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full border border-white dark:border-gray-900 ${agent.status === "active" ? "bg-green-500" : "bg-yellow-500"}`} />
            </div>
            <span className="text-[9px] text-gray-400 leading-tight text-center w-12 mt-0.5 line-clamp-2 break-all">
              {agent.displayName}
            </span>
            {/* Edit icon on hover */}
            <button
              onClick={(e) => { e.stopPropagation(); navigate(`/agents/edit/${agent.id}`); }}
              className="absolute -top-1 -right-1 w-4 h-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-sm"
              title={t("common.edit")}
            >
              <Settings2 className="w-2.5 h-2.5 text-gray-400" />
            </button>
          </div>
        ))}

        <div className="flex-1" />
        <button
          onClick={fetchAgents}
          disabled={loading}
          className="w-9 h-9 rounded-lg flex items-center justify-center text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-700"
          title={t("common.refresh")}
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
        </button>

        <ConfirmDialog
          open={!!pendingAction}
          title={t("skillEditor.unsavedChanges")}
          message={t("agentList.unsavedMessage")}
          confirmLabel={t("common.discard")}
          cancelLabel={t("skillEditor.stay")}
          danger
          onConfirm={() => { pendingAction?.(); setPendingAction(null); }}
          onCancel={() => setPendingAction(null)}
        />
      </div>
    );
  }

  // Full width mode
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">{t("agents.title")}</h3>
        <button
          onClick={fetchAgents}
          disabled={loading}
          className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-400 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
          title={t("common.refresh")}
        >
          {loading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {/* Meta-Agent */}
        <button
          onClick={() => handleSwitch("/agents")}
          className={`w-full text-left p-3 rounded-lg border transition-colors ${
            !agentId && !isEditing
              ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30"
              : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
          }`}
        >
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-blue-600 flex-shrink-0" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{t("agents.metaAgent")}</p>
              <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{t("agents.metaAgentDesc")}</p>
            </div>
          </div>
        </button>

        {agents.length > 0 && (
          <div className="text-xs text-gray-400 px-1 pt-2">{t("agents.createdAgents")}</div>
        )}

        {agents.map((agent) => (
          <div
            key={agent.id}
            className={`group w-full text-left p-3 rounded-lg border transition-colors ${
              agentId === agent.id
                ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30"
                : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"
            }`}
          >
            <div className="flex items-center justify-between">
              <button
                className="flex-1 text-left min-w-0"
                onClick={() => handleSwitch(`/agents/chat/${agent.id}`)}
              >
                <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate block">
                  {agent.displayName}
                </span>
              </button>
              <div className="flex items-center gap-1 flex-shrink-0">
                <button
                  onClick={(e) => { e.stopPropagation(); navigate(`/agents/edit/${agent.id}`); }}
                  className="p-1 text-gray-300 hover:text-blue-600 rounded transition-colors"
                  title={t("common.edit")}
                >
                  <Settings2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDuplicate(agent.id); }}
                  className="p-1 text-gray-300 hover:text-blue-600 rounded transition-colors"
                  title={t("agents.duplicate")}
                  disabled={actionLoading?.id === agent.id}
                >
                  {actionLoading?.id === agent.id && actionLoading.action === "duplicate" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmAction({ agentId: agent.id, agentName: agent.displayName, type: "archive" }); }}
                  className="p-1 text-gray-300 hover:text-orange-500 rounded transition-colors"
                  title={t("agents.archive")}
                  disabled={actionLoading?.id === agent.id}
                >
                  {actionLoading?.id === agent.id && actionLoading.action === "archive" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Archive className="w-3.5 h-3.5" />}
                </button>
                <span
                  className={`w-2 h-2 rounded-full ${
                    agent.status === "active" ? "bg-green-500" : "bg-yellow-500"
                  }`}
                  title={agent.status}
                />
              </div>
            </div>
            {agent.description && (
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">
                {agent.description}
              </p>
            )}
          </div>
        ))}

        {agents.length === 0 && !loading && (
          <div className="text-center text-gray-400 text-xs mt-4">
            <Bot className="w-6 h-6 mx-auto mb-1 opacity-30" />
            <p>{t("agents.noAgents")}</p>
          </div>
        )}

        {/* Archived agents */}
        {archivedAgents.length > 0 && (
          <>
            <button
              onClick={() => setShowArchived(!showArchived)}
              className="flex items-center gap-1 text-xs text-gray-400 px-1 pt-3 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <ChevronDown className={`w-3 h-3 transition-transform ${showArchived ? "" : "-rotate-90"}`} />
              <Archive className="w-3 h-3" />
              {t("agentList.archived", { count: archivedAgents.length })}
            </button>
            {showArchived && archivedAgents.map((agent) => (
              <div key={agent.id} className="group w-full text-left p-2.5 rounded-lg border border-dashed border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <Archive className="w-3 h-3 text-gray-400 flex-shrink-0" />
                    <span className="text-xs text-gray-500 dark:text-gray-400 truncate">{agent.displayName}</span>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => setConfirmAction({ agentId: agent.id, agentName: agent.displayName, type: "restore" })}
                      className="p-1 text-gray-400 hover:text-green-600 rounded transition-colors"
                      title={t("agents.restore")}
                      disabled={actionLoading?.id === agent.id}
                    >
                      {actionLoading?.id === agent.id && actionLoading.action === "restore" ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3" />}
                    </button>
                    <button
                      onClick={() => setConfirmAction({ agentId: agent.id, agentName: agent.displayName, type: "purge" })}
                      className="p-1 text-gray-400 hover:text-red-600 rounded transition-colors"
                      title={t("agents.deleteForever")}
                      disabled={actionLoading?.id === agent.id}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      <ConfirmDialog
        open={!!pendingAction}
        title={t("skillEditor.unsavedChanges")}
        message={t("agentList.unsavedMessage")}
        confirmLabel={t("common.discard")}
        cancelLabel={t("skillEditor.stay")}
        danger
        onConfirm={() => { pendingAction?.(); setPendingAction(null); }}
        onCancel={() => setPendingAction(null)}
      />

      <ConfirmDialog
        open={!!confirmAction}
        title={confirmAction?.type === "archive" ? t("agents.archive") : confirmAction?.type === "restore" ? t("agents.restore") : t("agents.deleteForever")}
        message={
          confirmAction?.type === "archive"
            ? t("agents.archiveConfirm", { name: confirmAction.agentName })
            : confirmAction?.type === "restore"
            ? t("agents.restoreConfirm", { name: confirmAction?.agentName })
            : t("agents.permanentDeleteConfirm", { name: confirmAction?.agentName })
        }
        confirmLabel={confirmAction?.type === "archive" ? t("agents.archive") : confirmAction?.type === "restore" ? t("agents.restore") : t("agents.deleteForever")}
        cancelLabel={t("common.cancel")}
        danger={confirmAction?.type !== "restore"}
        onConfirm={() => confirmAction && executeAgentAction(confirmAction.agentId, confirmAction.type)}
        onCancel={() => setConfirmAction(null)}
      />
    </div>
  );
}
