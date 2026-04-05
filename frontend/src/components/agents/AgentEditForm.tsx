import { useAgentEditStore } from "../../stores/agent-edit-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useEditAssistantStore } from "../../stores/edit-assistant-store";
import { Loader2, Save, Code2, Shield, Sparkles, FileDown, GitCompare, Wrench } from "lucide-react";
import { useMemo, useEffect, useState, useRef } from "react";
import { useParams, useNavigate } from "react-router";
import MonacoEditor from "@monaco-editor/react";
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

export default function AgentEditForm() {
  const { t } = useTranslation();
  const { agentId: routeAgentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const {
    agentId, agentName, formData, loading, saving,
    loadAgent, updateField, setSaving, markSaved, getChangedFields,
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
    onNavigateBack: () => navigate(-1),
  });

  const changedFields = useMemo(() => getChangedFields(), [formData, getChangedFields]);

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

  // Load agent data when route param changes
  useEffect(() => {
    if (routeAgentId) {
      const agent = agents.find(a => a.id === routeAgentId);
      loadAgent(routeAgentId, agent?.displayName || routeAgentId);
    }
  }, [routeAgentId]);

  if (!agentId || loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 dark:text-gray-400">
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
          agentId={agentId!}
          skill={editingSkill}
          onBack={() => {
            setEditingSkill(null)
            // Restore scroll position after React re-renders
            requestAnimationFrame(() => {
              if (formScrollRef.current) {
                formScrollRef.current.scrollTop = savedScrollTop.current
              }
            })
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
          <p className="text-[11px] text-gray-400">
            {isCreateMode ? t("agentEditor.createDesc", "Configure and deploy a new agent") : (
              <span className="flex items-center gap-1.5">
                <span className="font-mono text-[10px] text-gray-400 select-all">{agentId}</span>
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => openPanel(agentId!)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${panelOpen ? "bg-purple-50 text-purple-600" : "text-gray-500 hover:text-purple-600 hover:bg-purple-50"}`}
            title={t("assistant.title")}
          >
            <Sparkles className="w-3.5 h-3.5" />
          </button>
          {Object.keys(changedFields).length > 0 && (
            <button
              onClick={() => deploy.setShowReview("view")}
              className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
              title={t("agentEditor.viewCode")}
            >
              <GitCompare className="w-3.5 h-3.5" />
              Diff
            </button>
          )}
          <button
            onClick={deploy.handleValidateOnly}
            disabled={saving || deploy.validating}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors disabled:opacity-50"
            title={t("common.validate")}
          >
            {deploy.validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-3.5 h-3.5" />}
            {t("common.validate")}
          </button>
          <button
            onClick={deploy.handleSaveDraft}
            disabled={deploy.savingDraft}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors disabled:opacity-50"
            title={t("agentEditor.draft")}
          >
            {deploy.savingDraft ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
            {t("agentEditor.draft")}
          </button>
          <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-0.5" />
          <button
            onClick={() => navigate(-1)}
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
        {/* Status banner */}
        {deploy.status && (
          <div className={`rounded-lg text-sm font-medium ${deploy.status.includes("Error") || deploy.status.includes("failed") ? "bg-red-50 text-red-600 border border-red-200" : "bg-green-50 text-green-600 border border-green-200"}`}>
            <div className="px-4 py-3 flex items-center justify-between">
              <span>{deploy.status}</span>
            </div>
            {deploy.errorDetail && deploy.errorDetail !== "__hidden__" && (
              <details className="px-4 pb-3">
                <summary className="text-[11px] cursor-pointer opacity-70 hover:opacity-100">{t("agentEditor.showDetails")}</summary>
                <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap bg-red-100/50 rounded p-2 max-h-40 overflow-y-auto">{deploy.errorDetail}</pre>
              </details>
            )}
          </div>
        )}

        {/* Validation Results */}
        {deploy.validationResult && (deploy.validationResult.errors.length > 0 || deploy.validationResult.warnings.length > 0) && (
          <div className={`rounded-lg text-sm border ${!deploy.validationResult.valid ? "bg-red-50 border-red-200" : "bg-amber-50 border-amber-200"}`}>
            <div className="px-4 py-3">
              <p className={`font-medium ${deploy.validationResult.valid ? "text-amber-700" : "text-red-600"}`}>
                {!deploy.validationResult.valid
                  ? t("agentEditor.validationFailed")
                  : deploy.validationResult.warnings.length > 0
                    ? t("validation.passedWithWarnings", { count: deploy.validationResult.warnings.length })
                    : t("agentEditor.validationPassed")}
              </p>
              {deploy.validationResult.errors.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {deploy.validationResult.errors.map((e, i) => (
                    <li key={i} className="text-[11px] text-red-600 flex items-start gap-1.5">
                      <span className="text-red-400 mt-0.5 flex-shrink-0">&#x2716;</span>
                      <span className="prose prose-xs prose-red max-w-none [&_p]:m-0 [&_code]:text-red-700 [&_strong]:text-red-700"><ReactMarkdown>{e}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {deploy.validationResult.warnings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {deploy.validationResult.warnings.map((w, i) => (
                    <li key={i} className="text-[11px] text-amber-700 flex items-start gap-1.5">
                      <span className="text-amber-500 mt-0.5 flex-shrink-0">&#x26A0;</span>
                      <span className="prose prose-xs prose-amber max-w-none [&_p]:m-0 [&_code]:text-amber-800 [&_strong]:text-amber-800"><ReactMarkdown>{w}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {/* Prompt Quality Scores */}
              {deploy.validationResult.prompt_scores && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {Object.entries(deploy.validationResult.prompt_scores).map(([dim, score]) => (
                    <span key={dim} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                      score >= 4 ? "bg-green-100 text-green-700" : score >= 3 ? "bg-yellow-100 text-yellow-700" : "bg-red-100 text-red-700"
                    }`}>
                      {dim.replace(/_/g, " ")}: {score}/5
                    </span>
                  ))}
                  {deploy.validationResult.prompt_overall != null && (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${
                      deploy.validationResult.prompt_overall >= 4 ? "bg-green-200 text-green-800" : deploy.validationResult.prompt_overall >= 3 ? "bg-yellow-200 text-yellow-800" : "bg-red-200 text-red-800"
                    }`}>
                      overall: {deploy.validationResult.prompt_overall}/5
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
                  {deploy.autoFixing ? t("agentEditor.fixing", "Fixing...") : t("common.autoFix")}
                </button>
                <button
                  onClick={deploy.handlePreviewCode}
                  disabled={deploy.previewLoading || !deploy.pendingStagingKey}
                  className="flex items-center gap-1 px-3 py-1 text-[12px] font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
                >
                  {deploy.previewLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Code2 className="w-3 h-3" />}
                  {t("agentEditor.viewCode")}
                </button>
                {deploy.validationResult.valid && deploy.pendingStagingKey && (
                  <button
                    onClick={() => { deploy.doDeploy(deploy.pendingStagingKey!); }}
                    className="px-3 py-1 text-[12px] font-medium bg-amber-500 text-white rounded-lg hover:bg-amber-600"
                  >
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
            // Save scroll position before switching to skill editor
            if (formScrollRef.current) {
              savedScrollTop.current = formScrollRef.current.scrollTop
            }
            setEditingSkill(skill)
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
    {/* Code Preview modal */}
    {deploy.previewCode && (
      <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => deploy.setPreviewCode(null)}>
        <div className="bg-gray-900 rounded-xl w-[80vw] h-[85vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()} onWheel={e => e.stopPropagation()}>
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
            <span className="text-sm font-medium text-gray-200">{t("agentEditor.codePreview")}</span>
            <button onClick={() => deploy.setPreviewCode(null)} className="px-3 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded-lg">{t("common.close")}</button>
          </div>
          <div className="flex-1 overflow-hidden">
            <MonacoEditor
              value={deploy.previewCode}
              language="python"
              theme="vs-dark"
              options={{ readOnly: true, fontSize: 12, minimap: { enabled: true }, scrollBeyondLastLine: false, automaticLayout: true }}
            />
          </div>
        </div>
      </div>
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
