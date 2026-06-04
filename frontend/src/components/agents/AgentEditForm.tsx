import { useAgentEditStore } from "../../stores/agent-edit-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useEditAssistantStore } from "../../stores/edit-assistant-store";
import { Loader2, Save, Shield, Sparkles, GitCompare, Wrench } from "lucide-react";
import { useMemo, useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router";
import ReactMarkdown from "react-markdown";
import EditAssistant from "./EditAssistant";
import { preloadPyodide } from "../../lib/pyodide-checker";
import { useTranslation } from "react-i18next";
import SkillDiffModal from "./SkillDiffModal";
import type { AgentSkillEntry } from "../../lib/agent-metadata";
import ReviewChangesModal from "./ReviewChangesModal";
import AgentFormSections from "./AgentFormSections";
import SkillEditorView from "./SkillEditorView";
import { useAgentDeploy } from "../../hooks/useAgentDeploy";
import { toast } from "../../lib/toast";
import { formatDraftAge } from "../../lib/draft-autosave";

export default function AgentEditForm() {
  const { t } = useTranslation();
  const { agentId: routeAgentId, skillId: routeSkillId } = useParams<{ agentId: string; skillId: string }>();
  const navigate = useNavigate();
  const {
    agentId, agentName, formData, loading, saving,
    loadAgent, updateField, setSaving, markSaved, getChangedFields,
    setEditingSkillId, pendingSkillFiles, originalSkillFiles,
    restoredDraft, clearRestoredNotice,
  } = useAgentEditStore();
  const { agents, fetchAgents } = useAgentListStore();
  const { panelOpen, openPanel } = useEditAssistantStore();
  const [diffSkill, setDiffSkill] = useState<AgentSkillEntry | null>(null);
  const [editingSkill, setEditingSkill] = useState<AgentSkillEntry | null>(null);
  const formScrollRef = useRef<HTMLDivElement>(null);
  const savedScrollTop = useRef(0);

  const isCreateMode = agentId?.startsWith("draft-") || agentId === "__new__";

  const deploy = useAgentDeploy({
    agentId: agentId ?? null,
    agentName: agentName ?? null,
    formData: formData ?? null,
    isCreateMode: !!isCreateMode,
    updateField,
    setSaving,
    markSaved,
    fetchAgents,
    onNavigateBack: () => navigate(agentId && !isCreateMode ? `/agents/chat/${agentId}` : "/agents"),
  });

  const changedFields = useMemo(() => getChangedFields(), [formData, pendingSkillFiles, originalSkillFiles, getChangedFields]);

  // Auto-open AI assistant panel when editing
  useEffect(() => {
    if (agentId) openPanel(agentId);
    preloadPyodide();
  }, [agentId]);

  // Listen for skill diff modal open events
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.skill) setDiffSkill(detail.skill);
    };
    window.addEventListener("open-skill-diff", handler);
    return () => window.removeEventListener("open-skill-diff", handler);
  }, []);

  // Surface a one-off toast when loadAgent restores a draft. The flag
  // carries the agentId it was captured for — if the user has already
  // switched to a different agent by the time this effect runs (race
  // between set() and navigation), drop the toast instead of mis-
  // attributing it to the new agent.
  useEffect(() => {
    if (!restoredDraft) return;
    if (restoredDraft.agentId === agentId) {
      toast.info(t("common.draftRestored", { when: formatDraftAge(restoredDraft.ts, t) }));
    }
    clearRestoredNotice();
  }, [restoredDraft, agentId, clearRestoredNotice, t]);

  // Load agent data when route param changes
  useEffect(() => {
    if (routeAgentId) {
      // Skip loadAgent for draft IDs if store already has data (from openNewWithData)
      if (routeAgentId.startsWith("draft-") && agentId === routeAgentId && formData) {
        console.log("[EditForm] SKIP loadAgent for draft, mcp_targets:", formData.mcp_targets);
        return;
      }
      console.log("[EditForm] CALLING loadAgent for", routeAgentId, "agentId:", agentId, "formData:", !!formData);
      const agent = agents.find(a => a.id === routeAgentId);
      loadAgent(routeAgentId, agent?.displayName || routeAgentId);
    }
  }, [routeAgentId]);

  // Open skill editor when URL has skillId
  useEffect(() => {
    if (routeSkillId && formData?.skills) {
      const skill = (formData.skills).find(s => s.id === routeSkillId);
      if (skill && (editingSkill?.id !== routeSkillId)) {
        setEditingSkill(skill);
        setEditingSkillId(skill.id);
      }
    } else if (!routeSkillId && editingSkill) {
      setEditingSkill(null);
      setEditingSkillId(null);
      // Restore scroll position after form re-renders
      setTimeout(() => {
        formScrollRef.current?.scrollTo(0, savedScrollTop.current);
      }, 50);
    }
  }, [routeSkillId, formData]);

  if (!agentId || loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-500">
        {loading ? <Loader2 className="w-6 h-6 animate-spin" /> : null}
      </div>
    );
  }

  if (!formData) return null;

  return (
    <div className="flex h-full">
    <div className="flex flex-col flex-1 min-w-0 bg-gray-50/50 dark:bg-gray-800/50">
      {editingSkill ? (
        <SkillEditorView
          agentId={agentId}
          skill={editingSkill}
          onBack={() => {
            navigate(`/agents/edit/${agentId}`)
          }}
        />
      ) : (
      <>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div>
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {isCreateMode ? t("agentEditor.create") : (formData.display_name || agentName)}
          </h2>
          <p className="text-[11px] text-gray-400 dark:text-gray-500">
            {isCreateMode ? t("agentEditor.createDesc", "Configure and deploy a new agent") : (
              <span className="flex items-center gap-1.5">
                <span className="font-mono text-[10px] text-gray-400 dark:text-gray-500 select-all">{agentId}</span>
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => openPanel(agentId)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${panelOpen ? "bg-purple-50 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400" : "text-gray-500 dark:text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/30"}`}
            title={t("assistant.title")}
          >
            <Sparkles className="w-3.5 h-3.5" />
          </button>
          {Object.keys(changedFields).length > 0 && (
            <button
              onClick={() => deploy.setShowReview("view")}
              className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-colors"
              title={t("agentEditor.viewCode")}
            >
              <GitCompare className="w-3.5 h-3.5" />
              Diff
            </button>
          )}
          <button
            onClick={deploy.handleValidateOnly}
            disabled={saving || deploy.validating}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 dark:text-gray-400 hover:text-green-600 dark:hover:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/30 rounded-lg transition-colors disabled:opacity-50"
            title={t("common.validate")}
          >
            {deploy.validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-3.5 h-3.5" />}
            {t("common.validate")}
          </button>
          <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-0.5" />
          <button
            onClick={() => navigate(agentId ? `/agents/chat/${agentId}` : "/agents")}
            className="px-2.5 py-1.5 text-[12px] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={() => deploy.handleSave()}
            disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all"
          >
            {saving ? (
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"
                  strokeDasharray={`${2 * Math.PI * 10}`}
                  strokeDashoffset={`${2 * Math.PI * 10 * (1 - deploy.progressPct / 100)}`}
                  transform="rotate(-90 12 12)"
                  style={{ transition: "stroke-dashoffset 0.5s ease" }}
                />
              </svg>
            ) : <Save className="w-3.5 h-3.5" />}
            {saving && deploy.progressStep ? deploy.progressStep : (isCreateMode ? t("agentEditor.create") : t("agentEditor.update"))}
          </button>
        </div>
      </div>

      {/* Form */}
      <div ref={formScrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {/* Status banner — hide when validation results are showing */}
        {deploy.status && !(deploy.validationResult && (deploy.validationResult.errors.length > 0 || deploy.validationResult.warnings.length > 0)) && (
          <div className={`rounded-lg text-sm font-medium ${/error|failed|失败|错误/i.test(deploy.status) ? "bg-red-50 text-red-600 border border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800" : "bg-green-50 text-green-600 border border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800"}`}>
            <div className="px-4 py-3 flex items-center justify-between">
              <span>{deploy.status}</span>
            </div>
            {deploy.errorDetail && deploy.errorDetail !== "__hidden__" && (
              <details className="px-4 pb-3">
                <summary className="text-[11px] cursor-pointer opacity-70 hover:opacity-100">{t("agentEditor.showDetails")}</summary>
                <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap bg-red-100/50 dark:bg-red-900/20 rounded p-2 max-h-40 overflow-y-auto">{deploy.errorDetail}</pre>
              </details>
            )}
          </div>
        )}

        {/* Validation Results */}
        {deploy.validationResult && (deploy.validationResult.errors.length > 0 || deploy.validationResult.warnings.length > 0) && (
          <div className={`rounded-lg text-sm border-2 bg-white dark:bg-gray-900 ${!deploy.validationResult.valid ? "border-red-400 dark:border-red-700" : "border-amber-400 dark:border-amber-700"}`}>
            <div className="px-4 py-3">
              <p className={`font-medium ${deploy.validationResult.valid ? "text-amber-700 dark:text-amber-300" : "text-red-700 dark:text-red-300"}`}>
                {!deploy.validationResult.valid
                  ? t("agentEditor.validationFailed")
                  : deploy.validationResult.warnings.length > 0
                    ? t("validation.passedWithWarnings", { count: deploy.validationResult.warnings.length })
                    : t("agentEditor.validationPassed")}
              </p>
              {deploy.validationResult.errors.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {deploy.validationResult.errors.map((e, i) => (
                    <li key={i} className="text-xs text-gray-900 dark:text-gray-100 flex items-start gap-1.5">
                      <span className="text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0">&#x2716;</span>
                      <span className="prose prose-xs max-w-none [&_*]:!text-inherit [&_p]:m-0 [&_code]:!bg-gray-200 dark:[&_code]:!bg-gray-800 [&_code]:!px-1 [&_code]:!rounded [&_strong]:!font-semibold"><ReactMarkdown>{e}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {deploy.validationResult.warnings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {deploy.validationResult.warnings.map((w, i) => (
                    <li key={i} className="text-xs text-gray-900 dark:text-gray-100 flex items-start gap-1.5">
                      <span className="text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0">&#x26A0;</span>
                      <span className="prose prose-xs max-w-none [&_*]:!text-inherit [&_p]:m-0 [&_code]:!bg-gray-200 dark:[&_code]:!bg-gray-800 [&_code]:!px-1 [&_code]:!rounded [&_strong]:!font-semibold"><ReactMarkdown>{w}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {/* Prompt Quality Scores */}
              {deploy.validationResult.prompt_scores && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {Object.entries(deploy.validationResult.prompt_scores).map(([dim, score]) => (
                    <span key={dim} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                      score >= 4 ? "bg-green-100 dark:bg-green-900/50 text-green-800 dark:text-green-100" : score >= 3 ? "bg-yellow-100 dark:bg-yellow-900/50 text-yellow-800 dark:text-yellow-100" : "bg-red-100 dark:bg-red-900/50 text-red-800 dark:text-red-100"
                    }`}>
                      {t(`promptDimensions.${dim}`, dim.replace(/_/g, " "))}: {score}/5
                    </span>
                  ))}
                  {deploy.validationResult.prompt_overall != null && (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${
                      deploy.validationResult.prompt_overall >= 4 ? "bg-green-200 dark:bg-green-900/60 text-green-900 dark:text-white" : deploy.validationResult.prompt_overall >= 3 ? "bg-yellow-200 dark:bg-yellow-900/60 text-yellow-900 dark:text-white" : "bg-red-200 dark:bg-red-900/60 text-red-900 dark:text-white"
                    }`}>
                      {t("promptDimensions.overall")}: {deploy.validationResult.prompt_overall}/5
                    </span>
                  )}
                </div>
              )}
              {/* Action buttons */}
              <div className="mt-3 flex items-center gap-2">
                <button
                  onClick={deploy.dismissValidation}
                  className="px-3 py-1 text-[12px] text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
                >
                  {t("common.dismiss")}
                </button>
                <button
                  onClick={deploy.handleAutoFix}
                  disabled={deploy.autoFixing}
                  className="flex items-center gap-1 px-3 py-1 text-[12px] font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
                >
                  {deploy.autoFixing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wrench className="w-3 h-3" />}
                  {deploy.autoFixing ? t("agentEditor.fixing") : t("common.autoFix")}
                </button>
                {deploy.validationResult.valid && deploy.pendingStagingKey && (
                  <button
                    onClick={() => { deploy.doDeploy(deploy.pendingStagingKey!); }}
                    disabled={!!deploy.progressStep}
                    className="flex items-center gap-1 px-3 py-1 text-[12px] font-medium bg-amber-500 text-white rounded-lg hover:bg-amber-600 disabled:opacity-50"
                  >
                    {deploy.progressStep && <Loader2 className="w-3 h-3 animate-spin" />}
                    {t("agentEditor.deployAnyway")}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        <AgentFormSections
          formData={formData}
          agentId={agentId}
          agentName={agentName ?? null}
          isCreateMode={!!isCreateMode}
          changedFields={changedFields}
          updateField={updateField}
          handleOptimizeField={deploy.handleOptimizeField}
          deployedSkillHashes={formData.deployedSkillHashes}
          onEditSkill={(skill) => {
            savedScrollTop.current = formScrollRef.current?.scrollTop ?? 0
            navigate(`/agents/edit/${agentId}/skills/${skill.id}`)
          }}
        />
      </div>
      </>
      )}
    </div>
    {/* AI Assistant sidebar */}
    <EditAssistant />
    {/* Review Changes modal */}
    {deploy.showReview && (
      <ReviewChangesModal
        changes={changedFields}
        viewOnly={deploy.showReview === "view"}
        onConfirm={deploy.showReview === "deploy" ? () => { deploy.setShowReview(false); deploy.handleSave(); } : undefined}
        onCancel={() => deploy.setShowReview(false)}
      />
    )}
    {/* Skill Diff Modal */}
    {diffSkill && agentId && (
      <SkillDiffModal
        open={!!diffSkill}
        onClose={() => setDiffSkill(null)}
        agentId={agentId}
        skill={diffSkill}
      />
    )}
    </div>
  );
}
