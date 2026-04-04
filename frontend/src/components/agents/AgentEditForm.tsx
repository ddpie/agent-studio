import { useAgentEditStore } from "../../stores/agent-edit-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useEditAssistantStore } from "../../stores/edit-assistant-store";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { Loader2, Save, Plus, Trash2, Eye, EyeOff, Code2, MessageSquare, Settings2, Shield, Sparkles, Maximize2, Minimize2, FileDown, GitCompare, Wrench } from "lucide-react";
import { useState, useMemo, useRef, useEffect } from "react";
import { useParams, useNavigate } from "react-router";
import MonacoEditor, { DiffEditor } from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { MODEL_GROUPS } from "../../lib/models";
import { writeJsonToS3 } from "../../lib/s3-storage";
import ReactMarkdown from "react-markdown";
import EditAssistant from "./EditAssistant";
import { useUISettings } from "../../stores/ui-settings-store";
import { preloadPyodide, checkPythonSyntax, isPyodideReady } from "../../lib/pyodide-checker";
import { useTranslation } from "react-i18next";

function useIsDark() {
  const { theme } = useUISettings();
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

const TEMPLATE_OPTIONS = [
  { id: "", label: "None" },
  { id: "general", label: "General Assistant" },
  { id: "expert", label: "Professional Consultant" },
  { id: "customer_service", label: "Customer Service" },
  { id: "data_analyst", label: "Data Analyst" },
  { id: "creative_writer", label: "Creative Writer" },
];

const FIELD_LABELS: Record<string, string> = {
  name: "Name", display_name: "Display Name", description: "Description",
  system_prompt: "System Prompt", tool_definitions: "Tools", tool_names: "Tool Names",
  welcome_message: "Welcome Message", suggestions: "Suggestions", template_id: "Template",
  default_model_id: "Default Model", supports_images: "Image Support", model_id: "Model",
};

/** Extract structured tool results from a Meta-Agent stream response.
 *  Parses __tool result markers and decodes base64 output into JSON.
 *  Returns a map of toolName → parsed JSON result.
 */
function extractToolResults(rawStream: string): Record<string, unknown> {
  const results: Record<string, unknown> = {};
  const markerRe = /\{"__tool"\s*:\s*"result"\s*,\s*"name"\s*:\s*"([^"]+)"\s*,\s*"input"\s*:\s*"([^"]*)"\s*,\s*"output"\s*:\s*"([^"]*)"\s*\}/g;
  let match;
  while ((match = markerRe.exec(rawStream)) !== null) {
    const [, name, , outputB64] = match;
    if (outputB64) {
      try {
        const decoded = new TextDecoder().decode(Uint8Array.from(atob(outputB64), c => c.charCodeAt(0)));
        const parsed = JSON.parse(decoded);
        results[name] = parsed;
      } catch { /* skip unparseable */ }
    }
  }
  return results;
}

/** Stream a Meta-Agent prompt, collecting tool markers for progress and results. */
async function streamMetaAgent(
  prompt: string,
  onProgress?: (step: string, pct: number) => void,
  toolNameMap?: Record<string, string>,
  toolPctMap?: Record<string, number>,
): Promise<{ raw: string; toolResults: Record<string, unknown> }> {
  let raw = "";
  const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
  for await (const chunk of stream) {
    raw += chunk;
    if (onProgress) {
      const toolRe = /\{"__tool"[^}]*\}/g;
      let m;
      while ((m = toolRe.exec(chunk)) !== null) {
        try {
          const parsed = JSON.parse(m[0]);
          if (parsed.__tool === "start" && parsed.name) {
            const step = toolNameMap?.[parsed.name] || `Running ${parsed.name}...`;
            const pct = toolPctMap?.[parsed.name] || 0;
            onProgress(step, pct);
          }
        } catch { /* skip */ }
      }
    }
  }
  return { raw, toolResults: extractToolResults(raw) };
}

function ReviewChangesModal({ changes, onConfirm, onCancel, viewOnly }: {
  changes: Record<string, { old: string; new: string }>;
  onConfirm?: () => void;
  onCancel: () => void;
  viewOnly?: boolean;
}) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const entries = Object.entries(changes).filter(([k]) => !["tools", "tool_names", "created_at", "agent_id"].includes(k));
  const [activeIdx, setActiveIdx] = useState(0);

  // ESC to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  if (entries.length === 0) return null;

  const [key, { old: oldVal, new: newVal }] = entries[activeIdx];
  const label = FIELD_LABELS[key] || key;
  const lang = key === "tool_definitions" ? "python" : key === "system_prompt" ? "markdown" : "plaintext";

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onCancel}>
      <div className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl`} onClick={(e) => e.stopPropagation()}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
          <div className="flex items-center gap-3">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className={`text-sm font-semibold ${isDark ? "text-gray-200" : "text-gray-800"}`}>{viewOnly ? "Changes" : "Review Changes"}</span>
            <div className="flex items-center gap-1">
              {entries.map(([k], i) => (
                <button key={k} onClick={() => setActiveIdx(i)}
                  className={`px-2 py-0.5 text-[11px] rounded ${i === activeIdx
                    ? "bg-blue-600 text-white"
                    : isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"
                  }`}>
                  {FIELD_LABELS[k] || k}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onCancel} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{viewOnly ? "Close" : t("common.cancel")}</button>
            {!viewOnly && onConfirm && (
              <button onClick={onConfirm} className="px-4 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700">Confirm & Deploy</button>
            )}
          </div>
        </div>
        <div className={`px-4 py-1.5 text-xs font-semibold border-b ${isDark ? "text-gray-400 border-gray-700 bg-gray-800/50" : "text-gray-600 border-gray-200 bg-gray-50"}`}>
          {label}
        </div>
        <div className="flex-1 overflow-hidden">
          <DiffEditor
            original={oldVal}
            modified={newVal}
            language={lang}
            theme={isDark ? "vs-dark" : "light"}
            options={{
              readOnly: true,
              renderSideBySide: true,
              fontSize: 12,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
            }}
          />
        </div>
      </div>
    </div>
  );
}

function Section({ title, icon, action, children }: { title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-gray-50 dark:from-gray-800 to-white dark:to-gray-800 flex items-center gap-1.5">
        {icon && <span className="text-gray-400">{icon}</span>}
        <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">{title}</h3>
        {action && <span className="ml-auto">{action}</span>}
      </div>
      <div className="px-3 py-3 space-y-3">{children}</div>
    </div>
  );
}

function Field({ label, hint, changed, onOptimize, children }: { label: string; hint?: string; changed?: boolean; onOptimize?: () => void; children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <div>
      <div className="text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-0.5 flex items-center gap-1">
        {label}
        {changed && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" title={t("agentEditor.modified")} />}
        {onOptimize && (
          <button
            type="button"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onOptimize(); }}
            className="ml-auto w-4 h-4 flex items-center justify-center text-gray-300 hover:text-purple-500 transition-colors rounded"
            title={`AI optimize ${label}`}
          >
            <Sparkles className="w-2.5 h-2.5" />
          </button>
        )}
      </div>
      {children}
      {hint && <p className="text-[10px] text-gray-400 mt-0.5">{hint}</p>}
    </div>
  );
}

const inputClass = "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";
const disabledClass = "w-full px-2 py-1.5 border border-gray-100 dark:border-gray-700 rounded-lg text-[13px] bg-gray-50 dark:bg-gray-800 text-gray-400 cursor-not-allowed";

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
  const [status, setStatus] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const [progressStep, setProgressStep] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [showReview, setShowReview] = useState<false | "view" | "deploy">(false);
  const [validationResult, setValidationResult] = useState<{ valid: boolean; errors: string[]; warnings: string[]; prompt_scores?: Record<string, number>; prompt_overall?: number } | null>(null);
  const [validating, setValidating] = useState(false);
  const [pendingStagingKey, setPendingStagingKey] = useState<string | null>(null);
  const [autoFixing, setAutoFixing] = useState(false);
  const [previewCode, setPreviewCode] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Track which fields have changed
  const changedFields = useMemo(() => {
    return getChangedFields();
  }, [formData, getChangedFields]);

  // Auto-open AI assistant panel when editing, reload history on agent switch
  useEffect(() => {
    if (agentId) {
      openPanel(agentId);
    }
    preloadPyodide();
  }, [agentId]);

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

  const isCreateMode = agentId?.startsWith("draft-") || agentId === "__new__";

  const handleValidateOnly = async () => {
    setValidating(true);
    setStatus(null);
    setValidationResult(null);

    try {
      const stagingKey = `agents/_staging/${agentId || "new"}-val-${Date.now()}.json`;
      const stagingData = {
        name: formData.name || agentName,
        display_name: formData.display_name || agentName,
        description: formData.description || "",
        system_prompt: formData.system_prompt || "",
        tool_definitions: formData.tool_definitions || "",
        tool_names: formData.tool_names || "",
        template_id: formData.template_id || "",
        supports_images: formData.supports_images || false,
      };
      const uploaded = await writeJsonToS3(stagingKey, stagingData);
      if (!uploaded) { setStatus("Failed to upload config"); return; }

      const { toolResults } = await streamMetaAgent(
        `Execute validate_agent with staging_key: ${stagingKey}\nDo NOT ask for confirmation.`,
      );
      const validation = toolResults.validate_agent as { valid: boolean; errors: string[]; warnings: string[]; prompt_scores?: Record<string, number>; prompt_overall?: number } | undefined;

      if (validation) {
        setValidationResult(validation);
        setPendingStagingKey(stagingKey);
        if (validation.valid && validation.warnings.length === 0) {
          setStatus(t("agentEditor.validationPassed"));
        }
      } else {
        setStatus("Validation returned no result — Meta-Agent may not have called validate_agent");
      }
    } catch (err) {
      setStatus(`Validation error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setValidating(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setStatus(null);
    setErrorDetail(null);
    setValidationResult(null);

    try {
      // Upload full config to S3 staging to avoid token limits
      const stagingKey = `agents/_staging/${agentId || "new"}-${Date.now()}.json`;
      const stagingData = {
        name: formData.name || agentName,
        display_name: formData.display_name || agentName,
        description: formData.description || "",
        system_prompt: formData.system_prompt || "",
        tool_definitions: formData.tool_definitions || "",
        tool_names: formData.tool_names || "",
        welcome_message: formData.welcome_message || "",
        suggestions: Array.isArray(formData.suggestions) ? formData.suggestions : (formData.suggestions || "").split("|").filter(Boolean),
        template_id: formData.template_id || "",
        supports_images: formData.supports_images || false,
      };
      const uploaded = await writeJsonToS3(stagingKey, stagingData);
      if (!uploaded) {
        setStatus("Failed to upload config to S3");
        setSaving(false);
        return;
      }

      // Step 1: Validate
      setProgressStep("Validating...");
      setProgressPct(10);
      const valPrompt = `Execute validate_agent with staging_key: ${stagingKey}\nDo NOT ask for confirmation.`;
      const { toolResults: valToolResults } = await streamMetaAgent(valPrompt);
      setValidating(false);
      setProgressPct(30);

      // Extract structured validation result from tool marker
      const validation = valToolResults.validate_agent as { valid: boolean; errors: string[]; warnings: string[]; prompt_scores?: Record<string, number>; prompt_overall?: number } | undefined;

      if (validation) {
        setValidationResult(validation);
        setPendingStagingKey(stagingKey); // Always preserve for Auto-fix or Deploy anyway
        if (!validation.valid) {
          setProgressStep(null);
          setStatus("Validation failed — fix errors before deploying");
          setSaving(false);
          return;
        }
        // Has warnings but no errors — let user confirm
        if (validation.warnings.length > 0) {
          setProgressStep(null);
          setStatus(null);
          setSaving(false);
          return; // User will click "Deploy anyway" or "Auto-fix"
        }
      }

      // Step 2: Deploy (no errors, no warnings or validation failed to parse)
      await doDeploy(stagingKey);
    } catch (err) {
      setProgressStep(null);
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setSaving(false);
      setValidating(false);
    }
  };

  const doDeploy = async (stagingKey: string) => {
    setSaving(true);
    setStatus(null);
    setErrorDetail(null);
    setProgressStep(isCreateMode ? "Preparing..." : "Updating...");

    try {
      const prompt = isCreateMode
        ? `Execute create_agent with staging_key: ${stagingKey}
The full config is in S3. Read it and use those parameters.
- agent_name: ${formData.name || agentName}
- permission_tier: readonly

Do NOT ask for confirmation. Execute create_agent immediately.`
        : `Execute update_agent with these parameters:
- agent_id: ${agentId}
- staging_key: ${stagingKey}

The full config (system_prompt, tool_definitions, etc.) is in the S3 staging file. Pass staging_key to update_agent.
Do NOT ask for confirmation. Execute update_agent immediately.`;

      const deployToolNameMap: Record<string, string> = {
        create_agent: "Creating agent...",
        update_agent: "Updating agent...",
        upload_deployment: "Uploading code...",
        deploy_agent: "Deploying...",
        get_agent_status: "Checking status...",
        save_metadata: "Saving metadata...",
      };
      const deployToolPctMap: Record<string, number> = {
        validate_agent: 40, create_agent: 50, update_agent: 50,
        upload_deployment: 70, deploy_agent: 80, get_agent_status: 90, save_metadata: 95,
      };
      setProgressPct(35);

      const { toolResults } = await streamMetaAgent(
        prompt,
        (step, pct) => { setProgressStep(step); if (pct) setProgressPct(pct); },
        deployToolNameMap,
        deployToolPctMap,
      );

      // Extract structured result from tool markers
      const deployResult = (toolResults.update_agent || toolResults.create_agent) as { error?: string; status?: string; details?: string[] } | undefined;
      const failed = deployResult?.error != null;

      setProgressStep(null);
      setProgressPct(failed ? 0 : 100);
      setStatus(failed ? (isCreateMode ? t("agentEditor.createFailed") : t("agentEditor.updateFailed")) : (isCreateMode ? t("agentEditor.createSuccess") : t("agentEditor.updateSuccess")));

      if (failed) {
        const errorMsg = deployResult!.error!;
        const details = deployResult!.details?.join("\n") || "";
        setErrorDetail(details || errorMsg);
        setValidationResult({ valid: false, errors: [errorMsg, ...( deployResult!.details || [])], warnings: [] });
        setPendingStagingKey(stagingKey);
      } else {
        setErrorDetail(null);
      }
      fetchAgents();

      if (!failed) {
        markSaved();
        if (agentId && formData.tool_definitions) {
          const metaKey = `agents/${agentId}/metadata.json`;
          writeJsonToS3(metaKey, { ...formData, agent_id: agentId });
        }
        if (isCreateMode) navigate(-1);
      }
    } catch (err) {
      setProgressStep(null);
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  const handleAutoFix = async () => {
    if (!validationResult || !formData) return;
    setAutoFixing(true);

    // 1. Fix tool_names — always sync from tool_definitions
    const toolDefs = formData.tool_definitions || "";
    const funcNames = [...toolDefs.matchAll(/def\s+(\w+)\s*\(/g)].map(m => m[1]);
    if (funcNames.length > 0) {
      const currentNames = (formData.tool_names || "").split(",").map(s => s.trim()).filter(Boolean);
      const definedSet = new Set(funcNames);
      const currentSet = new Set(currentNames);
      if (funcNames.length !== currentNames.length || funcNames.some(n => !currentSet.has(n)) || currentNames.some(n => !definedSet.has(n))) {
        updateField("tool_names", funcNames.join(","));
        updateField("tools", funcNames);
      }
    }

    // 2. Pass ALL validation issues to AI assistant — generic, no per-field hardcoding
    const allIssues = [
      ...validationResult.errors.map(e => `ERROR: ${e}`),
      ...validationResult.warnings.map(w => `WARNING: ${w}`),
    ];

    if (allIssues.length > 0) {
      const { sendMessage, openPanel } = useEditAssistantStore.getState();
      openPanel(agentId!);
      const fixPrompt = `## Auto-Fix Task
Fix these validation issues:

${allIssues.map((issue, i) => `${i + 1}. ${issue}`).join("\n")}

tool_names should be: ${funcNames.join(",") || "(extract from @tool functions)"}`;

      await sendMessage(fixPrompt, { ...formData }, (updates) => {
        for (const [key, value] of Object.entries(updates)) {
          if (key === "tool_definitions" && typeof value === "string" && formData.tool_definitions) {
            const existingBlocks = (formData.tool_definitions).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
            const newBlocks = (value as string).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
            const merged = new Map<string, string>();
            for (const b of existingBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0,30); merged.set(n, b); }
            for (const b of newBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0,30); merged.set(n, b); }
            updateField("tool_definitions" as keyof typeof formData, Array.from(merged.values()).join("\n\n\n") as never);
          } else {
            updateField(key as keyof typeof formData, value as never);
          }
        }
      });
    }

    setValidationResult(null);
    setAutoFixing(false);
    setStatus(t("agentEditor.autoFixApplied"));
  };

  const handleOptimizeField = (fieldName: string, fieldLabel: string) => {
    const { sendMessage, openPanel } = useEditAssistantStore.getState();
    openPanel(agentId!);
    const prompts: Record<string, string> = {
      description: `Optimize the description field. Make it concise (1-2 sentences), clear, and descriptive. Keep the same language. Output __update JSON.`,
      display_name: `Optimize the display_name. Make it short, memorable, and descriptive. Keep the same language. Output __update JSON.`,
      welcome_message: `Optimize the welcome_message. Make it friendly, concise, and mention key capabilities. Keep the same language. Output __update JSON.`,
      suggestions: `Optimize the suggestions (quick-start prompts). Generate 3-5 practical, specific prompts that showcase the agent's main capabilities. Keep the same language. Output __update JSON with suggestions as an array.`,
      system_prompt: `Optimize the system_prompt following best practices:
1. Structure with ## headers: Role, Capabilities, Tool Usage, Constraints, Output Format
2. For each tool, add specific usage guidance ("When user asks X, use tool Y")
3. Add constraints with "NEVER" for critical rules
4. Add WRONG/CORRECT examples for common mistakes
5. Keep the same language as the current prompt
6. Preserve all existing capabilities and tool references
Output __update JSON.`,
      tool_definitions: `Review and optimize the tool code. For each tool:
1. Ensure docstring is clear and describes what the tool does
2. Ensure type hints are complete
3. Add error handling for common failures (network timeout, permission denied, empty results)
4. Keep code concise — no unnecessary comments or verbose patterns
Only output changed tools in tool_definitions. Output __update JSON.`,
    };
    const prompt = prompts[fieldName] || `Optimize the ${fieldLabel} field. Improve clarity and quality. Keep the same language. Output __update JSON.`;
    sendMessage(prompt, { ...formData! }, (updates) => {
      for (const [key, value] of Object.entries(updates)) {
        updateField(key as keyof typeof formData, value as never);
      }
    });
  };

  const handleSaveDraft = async () => {
    setSavingDraft(true);
    setStatus(null);
    try {
      const draftKey = isCreateMode
        ? `agents/_drafts/${formData.name || "untitled"}/metadata.json`
        : `agents/${agentId}/draft.json`;
      const draftData = {
        ...formData,
        _draftSavedAt: new Date().toISOString(),
        _agentId: agentId,
        _agentName: agentName,
      };
      const ok = await writeJsonToS3(draftKey, draftData);
      setStatus(ok ? t("agentEditor.draftSaved") : t("agentEditor.draftFailed"));
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setSavingDraft(false);
    }
  };

  return (
    <div className="flex h-full">
    <div className="flex flex-col flex-1 min-w-0 bg-gray-50/50 dark:bg-gray-800/50">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <div>
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {isCreateMode ? "Create Agent" : (formData.display_name || agentName)}
          </h2>
          <p className="text-[11px] text-gray-400">
            {isCreateMode ? "Configure and deploy a new agent" : (
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
            title="AI Assistant"
          >
            <Sparkles className="w-3.5 h-3.5" />
          </button>
          {Object.keys(changedFields).length > 0 && (
            <button
              onClick={() => setShowReview("view")}
              className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
              title="View changes diff"
            >
              <GitCompare className="w-3.5 h-3.5" />
              Diff
            </button>
          )}
          <button
            onClick={handleValidateOnly}
            disabled={saving || validating}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-green-600 hover:bg-green-50 rounded-lg transition-colors disabled:opacity-50"
            title="Validate configuration"
          >
            {validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-3.5 h-3.5" />}
            {t("common.validate")}
          </button>
          <button
            onClick={handleSaveDraft}
            disabled={savingDraft}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors disabled:opacity-50"
            title="Save draft"
          >
            {savingDraft ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
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
            onClick={() => handleSave()}
            disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all"
          >
            {saving ? (
              <svg className="w-4 h-4" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25" />
                <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"
                  strokeDasharray={`${2 * Math.PI * 10}`}
                  strokeDashoffset={`${2 * Math.PI * 10 * (1 - progressPct / 100)}`}
                  transform="rotate(-90 12 12)"
                  style={{ transition: "stroke-dashoffset 0.5s ease" }}
                />
              </svg>
            ) : <Save className="w-3.5 h-3.5" />}
            {saving && progressStep ? progressStep : (isCreateMode ? "Create" : "Update")}
          </button>
        </div>
      </div>

      {/* Form */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {status && (
          <div className={`rounded-lg text-sm font-medium ${status.includes("Error") || status.includes("failed") ? "bg-red-50 text-red-600 border border-red-200" : "bg-green-50 text-green-600 border border-green-200"}`}>
            <div className="px-4 py-3 flex items-center justify-between">
              <span>{status}</span>
              {errorDetail && (
                <button
                  onClick={() => setErrorDetail(errorDetail === "__hidden__" ? errorDetail : "__hidden__")}
                  className="text-[11px] underline opacity-70 hover:opacity-100"
                >
                  {/* Toggle is handled by details element below */}
                </button>
              )}
            </div>
            {errorDetail && errorDetail !== "__hidden__" && (
              <details className="px-4 pb-3">
                <summary className="text-[11px] cursor-pointer opacity-70 hover:opacity-100">Show details</summary>
                <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap bg-red-100/50 rounded p-2 max-h-40 overflow-y-auto">{errorDetail}</pre>
              </details>
            )}
          </div>
        )}

        {/* Validation Results */}
        {validationResult && (validationResult.errors.length > 0 || validationResult.warnings.length > 0) && (
          <div className={`rounded-lg text-sm border ${!validationResult.valid ? "bg-red-50 border-red-200" : "bg-amber-50 border-amber-200"}`}>
            <div className="px-4 py-3">
              <p className={`font-medium ${validationResult.valid ? "text-amber-700" : "text-red-600"}`}>
                {!validationResult.valid
                  ? "Validation failed"
                  : validationResult.warnings.length > 0
                    ? `Validation passed with ${validationResult.warnings.length} warning(s)`
                    : "Validation passed"}
              </p>
              {validationResult.errors.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {validationResult.errors.map((e, i) => (
                    <li key={i} className="text-[11px] text-red-600 flex items-start gap-1.5">
                      <span className="text-red-400 mt-0.5 flex-shrink-0">&#x2716;</span>
                      <span className="prose prose-xs prose-red max-w-none [&_p]:m-0 [&_code]:text-red-700 [&_strong]:text-red-700"><ReactMarkdown>{e}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {validationResult.warnings.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {validationResult.warnings.map((w, i) => (
                    <li key={i} className="text-[11px] text-amber-700 flex items-start gap-1.5">
                      <span className="text-amber-500 mt-0.5 flex-shrink-0">&#x26A0;</span>
                      <span className="prose prose-xs prose-amber max-w-none [&_p]:m-0 [&_code]:text-amber-800 [&_strong]:text-amber-800"><ReactMarkdown>{w}</ReactMarkdown></span>
                    </li>
                  ))}
                </ul>
              )}
              {/* Prompt Quality Scores */}
              {validationResult.prompt_scores && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {Object.entries(validationResult.prompt_scores).map(([dim, score]) => (
                    <span key={dim} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${
                      score >= 4 ? "bg-green-100 text-green-700" : score >= 3 ? "bg-yellow-100 text-yellow-700" : "bg-red-100 text-red-700"
                    }`}>
                      {dim.replace(/_/g, " ")}: {score}/5
                    </span>
                  ))}
                  {validationResult.prompt_overall != null && (
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${
                      validationResult.prompt_overall >= 4 ? "bg-green-200 text-green-800" : validationResult.prompt_overall >= 3 ? "bg-yellow-200 text-yellow-800" : "bg-red-200 text-red-800"
                    }`}>
                      overall: {validationResult.prompt_overall}/5
                    </span>
                  )}
                </div>
              )}
              {/* Action buttons: always show Auto-fix; Deploy anyway only when valid (warnings only) */}
              <div className="mt-3 flex items-center gap-2">
                <button
                  onClick={() => { setValidationResult(null); setPendingStagingKey(null); }}
                  className="px-3 py-1 text-[12px] text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
                >
                  {t("common.dismiss")}
                </button>
                <button
                  onClick={handleAutoFix}
                  disabled={autoFixing}
                  className="flex items-center gap-1 px-3 py-1 text-[12px] font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
                >
                  {autoFixing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wrench className="w-3 h-3" />}
                  {autoFixing ? "Fixing..." : t("common.autoFix")}
                </button>
                <button
                  onClick={async () => {
                    if (!pendingStagingKey) return;
                    setPreviewLoading(true);
                    try {
                      const prompt = `Execute preview_assembled_code with staging_key: ${pendingStagingKey}\nReturn ONLY the JSON result. Do NOT include the code in your response.`;
                      let result = "";
                      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
                      for await (const chunk of stream) { result += chunk; }
                      const clean = result.replace(/\{"__tool"[^}]*\}/g, "");
                      // Extract preview_key from response
                      const keyMatch = clean.match(/"preview_key"\s*:\s*"([^"]+)"/);
                      if (keyMatch) {
                        // Fetch code from S3
                        const { readBinaryFromS3 } = await import("../../lib/s3-storage");
                        const buf = await readBinaryFromS3(keyMatch[1]);
                        if (buf) setPreviewCode(new TextDecoder().decode(buf));
                      }
                    } finally { setPreviewLoading(false); }
                  }}
                  disabled={previewLoading || !pendingStagingKey}
                  className="flex items-center gap-1 px-3 py-1 text-[12px] font-medium text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50"
                >
                  {previewLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Code2 className="w-3 h-3" />}
                  {t("agentEditor.viewCode")}
                </button>
                {validationResult.valid && pendingStagingKey && (
                  <button
                    onClick={() => { setValidationResult(null); doDeploy(pendingStagingKey!); setPendingStagingKey(null); }}
                    className="px-3 py-1 text-[12px] font-medium bg-amber-500 text-white rounded-lg hover:bg-amber-600"
                  >
                    {t("agentEditor.deployAnyway")}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Basic Info */}
        <Section title={t("agentEditor.basicInfo")} icon={<Settings2 className="w-3.5 h-3.5" />}>
          <div className="grid grid-cols-2 gap-4">
            <Field label={t("agentEditor.name")} changed={!!changedFields.name} hint={isCreateMode ? t("agentEditor.nameHint") : t("agentEditor.nameFixed")}>
              <input
                type="text"
                value={formData.name || agentName || ""}
                onChange={isCreateMode ? (e) => updateField("name", e.target.value) : undefined}
                disabled={!isCreateMode}
                className={isCreateMode ? inputClass : disabledClass}
              />
            </Field>
            <Field label={t("agentEditor.displayName")} changed={!!changedFields.display_name} hint={t("agentEditor.displayNameHint")} onOptimize={() => handleOptimizeField("display_name", "Display Name")}>
              <input
                type="text"
                value={formData.display_name || ""}
                onChange={(e) => updateField("display_name", e.target.value)}
                placeholder={formData.name || agentName || ""}
                className={inputClass}
              />
            </Field>
          </div>
          <Field label={t("agentEditor.description")} changed={!!changedFields.description} onOptimize={() => handleOptimizeField("description", "Description")}>
            <textarea
              value={formData.description || ""}
              onChange={(e) => updateField("description", e.target.value)}
              rows={2}
              className={inputClass + " resize-none"}
              placeholder={t("agentEditor.descriptionHint")}
            />
          </Field>
        </Section>

        {/* Chat Settings */}
        <Section title={t("agentEditor.chatSettings")} icon={<MessageSquare className="w-3.5 h-3.5" />}>
          <Field label={t("agentEditor.welcomeMessage")} changed={!!changedFields.welcome_message} hint={t("agentEditor.welcomeMessageHint")} onOptimize={() => handleOptimizeField("welcome_message", "Welcome Message")}>
            <textarea
              value={formData.welcome_message || ""}
              onChange={(e) => updateField("welcome_message", e.target.value)}
              rows={2}
              className={inputClass + " resize-none"}
              placeholder="Hello! I can help you with..."
            />
          </Field>
          <Field label={t("agentEditor.suggestions")} changed={!!changedFields.suggestions} hint={t("agentEditor.suggestionsHint")} onOptimize={() => handleOptimizeField("suggestions", "Suggested Prompts")}>
            <textarea
              value={(Array.isArray(formData.suggestions) ? formData.suggestions : (formData.suggestions || "").split("|").filter(Boolean)).join("\n")}
              onChange={(e) => updateField("suggestions", e.target.value.split("\n").filter(Boolean))}
              rows={3}
              className={inputClass + " resize-none"}
              placeholder={"What can you do?\nHelp me with...\nShow me an example"}
            />
          </Field>
        </Section>

        {/* Agent Behavior */}
        <Section title={t("agentEditor.agentBehavior")} icon={<Settings2 className="w-3.5 h-3.5" />}>
          <div className="grid grid-cols-3 gap-4">
            <Field label="Template" changed={!!changedFields.template_id}>
              <select
                value={formData.template_id || ""}
                onChange={(e) => updateField("template_id", e.target.value)}
                className={inputClass}
              >
                {TEMPLATE_OPTIONS.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Default Model" changed={!!changedFields.default_model_id} hint={t("agentEditor.modelHint")}>
              <select
                value={formData.default_model_id || ""}
                onChange={(e) => updateField("default_model_id", e.target.value)}
                className={inputClass}
              >
                <option value="">Auto (inherit)</option>
                {MODEL_GROUPS.map((g) =>
                  g.models.map((m) => (
                    <option key={m.id} value={m.id}>{m.label}</option>
                  ))
                )}
              </select>
            </Field>
            <Field label="Image Support" changed={!!changedFields.supports_images}>
              <label className={`flex items-center gap-2 h-[34px] px-3 border rounded-lg cursor-pointer transition-colors ${formData.supports_images ? "bg-blue-50 border-blue-300 text-blue-700" : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800"}`}>
                <input
                  type="checkbox"
                  checked={formData.supports_images || false}
                  onChange={(e) => updateField("supports_images", e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-[13px]">{formData.supports_images ? `${t("agentEditor.multimodal")} (enabled)` : t("agentEditor.multimodal")}</span>
              </label>
            </Field>
          </div>
          <Field label={t("agentEditor.systemPrompt")} changed={!!changedFields.system_prompt} hint={t("agentEditor.systemPromptHint")} onOptimize={() => handleOptimizeField("system_prompt", "System Prompt")}>
            <textarea
              value={formData.system_prompt || ""}
              onChange={(e) => updateField("system_prompt", e.target.value)}
              rows={14}
              className={inputClass + " font-mono text-xs leading-relaxed resize-y"}
              placeholder={t("agentEditor.systemPromptPlaceholder")}
            />
          </Field>
        </Section>

        {/* Tools */}
        <Section title={t("agentEditor.tools")} icon={<Code2 className="w-3.5 h-3.5" />} action={
          <button
            onClick={() => handleOptimizeField("tool_definitions", "Tools")}
            className="p-0.5 text-gray-300 hover:text-purple-500 transition-colors"
            title="AI optimize tools"
          >
            <Sparkles className="w-3 h-3" />
          </button>
        }>
          <ToolsEditor
            value={formData.tool_definitions || ""}
            onChange={(defs, names) => {
              updateField("tool_definitions", defs);
              updateField("tool_names", names);
              updateField("tools", names.split(",").map(s => s.trim()).filter(Boolean));
            }}
            onOptimizeTool={(toolName, toolCode) => {
              const { sendMessage, openPanel } = useEditAssistantStore.getState();
              openPanel(agentId!);
              sendMessage(
                `Optimize the tool \`${toolName}\`. Current code:\n\`\`\`python\n${toolCode}\n\`\`\`\n\nImprove:\n1. Docstring: clear, describes purpose, args, and return value\n2. Type hints: complete for all parameters and return\n3. Error handling: handle common failures (timeout, permission denied, empty results)\n4. Code quality: concise, no unnecessary comments\nOnly output this one tool in tool_definitions. Output __update JSON.`,
                { ...formData },
                (updates) => {
                  for (const [key, value] of Object.entries(updates)) {
                    if (key === "tool_definitions" && typeof value === "string" && formData.tool_definitions) {
                      const existingBlocks = (formData.tool_definitions).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
                      const newBlocks = (value as string).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
                      const merged = new Map<string, string>();
                      for (const b of existingBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0,30); merged.set(n, b); }
                      for (const b of newBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0,30); merged.set(n, b); }
                      updateField("tool_definitions" as keyof typeof formData, Array.from(merged.values()).join("\n\n\n") as never);
                    } else {
                      updateField(key as keyof typeof formData, value as never);
                    }
                  }
                }
              );
            }}
          />
          <Field label="Registered Tools" changed={!!changedFields.tool_names} hint="Auto-synced from tool code. Shows which tools will be available at runtime.">
            <div className={`w-full px-2 py-1.5 border border-gray-100 dark:border-gray-700 rounded-lg text-[12px] bg-gray-50 dark:bg-gray-800 min-h-[28px] flex flex-wrap gap-1 ${!formData.tool_names ? "italic text-gray-400" : ""}`}>
              {formData.tool_names
                ? (() => {
                    const allNames = formData.tool_names!.split(",").map(t => t.trim()).filter(Boolean);
                    return allNames.map(name => {
                      return (
                        <span key={name} className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-mono bg-blue-50 text-blue-700 border border-blue-200">
                          {name}
                        </span>
                      );
                    });
                  })()
                : "No tools registered"}
            </div>
          </Field>
        </Section>

        {/* Secrets */}
        {!isCreateMode && (
          <SecretsSection agentId={agentId!} />
        )}
      </div>
    </div>
    {/* AI Assistant sidebar */}
    <EditAssistant />
    {/* Review Changes modal */}
    {showReview && (
      <ReviewChangesModal
        changes={changedFields}
        viewOnly={showReview === "view"}
        onConfirm={showReview === "deploy" ? () => { setShowReview(false); handleSave(); } : undefined}
        onCancel={() => setShowReview(false)}
      />
    )}
    {/* Code Preview modal */}
    {previewCode && (
      <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setPreviewCode(null)}>
        <div className="bg-gray-900 rounded-xl w-[80vw] h-[85vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()} onWheel={e => e.stopPropagation()}>
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
            <span className="text-sm font-medium text-gray-200">Assembled Code Preview (main.py)</span>
            <button onClick={() => setPreviewCode(null)} className="px-3 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded-lg">Close</button>
          </div>
          <div className="flex-1 overflow-hidden">
            <MonacoEditor
              value={previewCode}
              language="python"
              theme="vs-dark"
              options={{ readOnly: true, fontSize: 12, minimap: { enabled: true }, scrollBeyondLastLine: false, automaticLayout: true }}
            />
          </div>
        </div>
      </div>
    )}
    </div>
  );
}

/** Extract function name from a @tool code block */
function extractFuncName(code: string): string {
  const m = code.match(/def\s+(\w+)\s*\(/);
  return m ? m[1] : "unnamed";
}

/** Extract first line of docstring as description */
function extractDocstring(code: string): string {
  const m = code.match(/"""(.+?)"""|'''(.+?)'''/s);
  if (!m) return "";
  const raw = (m[1] || m[2]).trim();
  // Take first line only
  const firstLine = raw.split("\n")[0].trim();
  return firstLine.length > 60 ? firstLine.slice(0, 60) + "..." : firstLine;
}

/** Split combined tool_definitions into individual tool blocks */
function splitTools(defs: string): string[] {
  if (!defs.trim()) return [];
  const parts = defs.split(/\n(?=@tool\b)/);
  const result: string[] = [];
  let prefix = "";
  for (const p of parts) {
    const trimmed = p.trim();
    if (!trimmed) continue;
    // If this block doesn't start with @tool, it's a preamble (imports etc.)
    // Prepend it to the next tool block
    if (!trimmed.startsWith("@tool")) {
      prefix = trimmed + "\n\n";
    } else {
      result.push(prefix + trimmed);
      prefix = "";
    }
  }
  return result;
}

/** Combine individual tool blocks into one string */
function joinTools(blocks: string[]): { defs: string; names: string } {
  const defs = blocks.filter(Boolean).join("\n\n\n");
  const names = blocks.filter(Boolean).map(extractFuncName).filter(n => n !== "unnamed").join(",");
  return { defs, names };
}

const TOOL_TEMPLATE = `@tool
def my_tool(query: str) -> str:
    """Description of what this tool does.

    Args:
        query: The input parameter.

    Returns:
        Result as string.
    """
    return "result"`;

function ToolsEditor({ value, onChange, onOptimizeTool }: {
  value: string;
  onChange: (defs: string, names: string) => void;
  onOptimizeTool?: (toolName: string, toolCode: string) => void;
}) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const [blocks, setBlocks] = useState<string[]>(() => {
    const initial = splitTools(value);
    return initial.length > 0 ? initial : [];
  });

  // Sync blocks when value changes externally (e.g., from AI assistant __update)
  // Compare by splitting — avoids false triggers from whitespace differences
  const prevValueRef = useRef(value);
  const internalUpdateRef = useRef(false);
  useEffect(() => {
    if (internalUpdateRef.current) {
      internalUpdateRef.current = false;
      prevValueRef.current = value;
      return;
    }
    if (value !== prevValueRef.current) {
      prevValueRef.current = value;
      const newBlocks = splitTools(value);
      setBlocks(newBlocks.length > 0 ? newBlocks : []);
    }
  }, [value]);

  const sync = (updated: string[]) => {
    setBlocks(updated);
    const { defs, names } = joinTools(updated);
    internalUpdateRef.current = true; // Mark as internal update to skip useEffect
    onChange(defs, names);
  };

  const updateBlock = (idx: number, code: string) => {
    const updated = [...blocks];
    updated[idx] = code;
    sync(updated);
  };

  const [confirmDeleteIdx, setConfirmDeleteIdx] = useState<number | null>(null);

  const removeBlock = (idx: number) => {
    setConfirmDeleteIdx(idx);
  };

  const confirmRemove = () => {
    if (confirmDeleteIdx !== null) {
      sync(blocks.filter((_, i) => i !== confirmDeleteIdx));
      setConfirmDeleteIdx(null);
    }
  };

  const addBlock = () => {
    sync([...blocks, TOOL_TEMPLATE]);
  };

  const [collapsed, setCollapsed] = useState<Record<number, boolean>>(() => {
    // Default all tools to collapsed
    const initial: Record<number, boolean> = {};
    blocks.forEach((_, i) => { initial[i] = true; });
    return initial;
  });
  const [fullscreenIdx, setFullscreenIdx] = useState<number | null>(null);

  const toggleCollapse = (idx: number) => {
    setCollapsed((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  // Fullscreen overlay
  if (fullscreenIdx !== null && blocks[fullscreenIdx] !== undefined) {
    const code = blocks[fullscreenIdx];
    const name = extractFuncName(code);
    const desc = extractDocstring(code);
    return (
      <div className="fixed inset-0 z-50 bg-gray-900 flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
          <span className="text-sm font-mono text-gray-200">
            <span className="text-blue-400">@tool</span> {name}
            {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
          </span>
          <button
            onClick={() => setFullscreenIdx(null)}
            className="flex items-center gap-1 px-2 py-1 text-xs text-gray-400 hover:text-white bg-gray-700 rounded hover:bg-gray-600 transition-colors"
          >
            <Minimize2 className="w-3.5 h-3.5" /> {t("agentEditor.exitFullscreen")}
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <MonacoEditor
            value={code}
            onChange={(v) => { if (v !== undefined && v !== code) updateBlock(fullscreenIdx, v); }}
            language="python"
            theme="vs-dark"
            onMount={(editor, monaco) => {
              if (isPyodideReady() && code) {
                const model = editor.getModel();
                if (model) {
                  const errors = checkPythonSyntax(code).map(e => ({
                    startLineNumber: e.line, endLineNumber: e.line,
                    startColumn: e.col || 1, endColumn: 1000,
                    message: e.msg,
                    severity: 8 as unknown as MonacoNS.MarkerSeverity,
                  }));
                  monaco.editor.setModelMarkers(model, "python-lint", errors);
                }
              }
            }}
            onValidate={() => {
              if (!isPyodideReady() || !code) return;
              const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
              if (!monacoInstance) return;
              const model = monacoInstance.editor.getModels().find(m => m.getValue() === code);
              if (!model) return;
              const errors = checkPythonSyntax(code).map(e => ({
                startLineNumber: e.line, endLineNumber: e.line,
                startColumn: e.col || 1, endColumn: 1000,
                message: e.msg,
                severity: 8 as unknown as MonacoNS.MarkerSeverity,
              }));
              monacoInstance.editor.setModelMarkers(model, "python-lint", errors);
            }}
            options={{ fontSize: 13, minimap: { enabled: true }, scrollBeyondLastLine: false, automaticLayout: true }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {blocks.length === 0 && (
        <p className="text-xs text-gray-400 italic">{t("agentEditor.noTools")}</p>
      )}
      {blocks.map((code, idx) => {
        const name = extractFuncName(code);
        const desc = extractDocstring(code);
        const isCollapsed = collapsed[idx] ?? false;
        return (
          <div key={idx} className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div
              className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700 cursor-pointer select-none"
              onClick={() => toggleCollapse(idx)}
            >
              <span className="text-xs font-mono text-gray-300 truncate">
                <span className="text-gray-500 mr-1">{isCollapsed ? "▶" : "▼"}</span>
                <span className="text-blue-400">@tool</span> {name !== "unnamed" ? name : <span className="text-gray-500 italic">unnamed</span>}
                {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
              </span>
              <div className="flex items-center gap-1">
                {onOptimizeTool && (
                  <button
                    onClick={(e) => { e.stopPropagation(); onOptimizeTool(name, code); }}
                    className="p-1 text-gray-500 hover:text-purple-400 transition-colors"
                    title="AI optimize this tool"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                  </button>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); setFullscreenIdx(idx); }}
                  className="p-1 text-gray-500 hover:text-white transition-colors"
                  title="Fullscreen"
                >
                  <Maximize2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); removeBlock(idx); }}
                  className="p-1 text-gray-500 hover:text-red-400 transition-colors"
                  title={t("agentEditor.removeTool")}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            {!isCollapsed && (
              <div style={{ height: "300px" }}>
                <MonacoEditor
                  value={code}
                  onChange={(v) => { if (v !== undefined && v !== code) updateBlock(idx, v); }}
                  language="python"
                  theme={isDark ? "vs-dark" : "light"}
                  onMount={(editor, monaco) => {
                    if (isPyodideReady() && code) {
                      const model = editor.getModel();
                      if (model) {
                        const errors = checkPythonSyntax(code).map(e => ({
                          startLineNumber: e.line, endLineNumber: e.line,
                          startColumn: e.col || 1, endColumn: 1000,
                          message: e.msg,
                          severity: 8 as unknown as MonacoNS.MarkerSeverity,
                        }));
                        monaco.editor.setModelMarkers(model, "python-lint", errors);
                      }
                    }
                  }}
                  onValidate={() => {
                    if (!isPyodideReady() || !code) return;
                    const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
                    if (!monacoInstance) return;
                    const model = monacoInstance.editor.getModels().find(m => m.getValue() === code);
                    if (!model) return;
                    const errors = checkPythonSyntax(code).map(e => ({
                      startLineNumber: e.line, endLineNumber: e.line,
                      startColumn: e.col || 1, endColumn: 1000,
                      message: e.msg,
                      severity: 8 as unknown as MonacoNS.MarkerSeverity,
                    }));
                    monacoInstance.editor.setModelMarkers(model, "python-lint", errors);
                  }}
                  options={{ fontSize: 12, minimap: { enabled: false }, scrollBeyondLastLine: false, automaticLayout: true, tabSize: 4 }}
                />
              </div>
            )}
          </div>
        );
      })}
      <button
        onClick={addBlock}
        className="flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:text-blue-700 px-3 py-2 border border-dashed border-blue-300 rounded-lg hover:bg-blue-50 transition-colors w-full justify-center"
      >
        <Plus className="w-3.5 h-3.5" /> {t("agentEditor.addTool")}
      </button>
      {/* Delete confirmation modal */}
      {confirmDeleteIdx !== null && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDeleteIdx(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">Delete tool?</p>
            <p className="text-xs text-gray-500 mb-4">
              {t("agentEditor.deleteToolConfirm")}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDeleteIdx(null)} className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg">{t("common.cancel")}</button>
              <button onClick={confirmRemove} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SecretsSection({ agentId }: { agentId: string }) {
  const [secrets, setSecrets] = useState<Array<{ key: string; value: string }>>([]);
  const [saving, setSaving] = useState(false);
  const [showValues, setShowValues] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const addRow = () => setSecrets([...secrets, { key: "", value: "" }]);
  const removeRow = (idx: number) => setSecrets(secrets.filter((_, i) => i !== idx));
  const updateRow = (idx: number, field: "key" | "value", val: string) => {
    setSecrets(secrets.map((s, i) => i === idx ? { ...s, [field]: val } : s));
  };

  const saveSecrets = async () => {
    const valid = secrets.filter((s) => s.key && s.value);
    if (valid.length === 0) return;
    setSaving(true);
    setStatus(null);
    try {
      const secretsObj: Record<string, string> = {};
      for (const s of valid) secretsObj[s.key] = s.value;
      const prompt = `Execute set_agent_secrets with these parameters:
- agent_id: ${agentId}
- secrets: ${JSON.stringify(JSON.stringify(secretsObj))}

Do NOT ask for confirmation. Execute immediately.`;
      let result = "";
      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
      for await (const chunk of stream) result += chunk;
      setStatus(result.includes("error") ? "Failed to save secrets" : "Secrets saved");
      if (!result.includes("error")) setSecrets(valid.map((s) => ({ key: s.key, value: "" })));
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section title="Secrets" icon={<Shield className="w-3.5 h-3.5" />}>
      {status && (
        <div className={`px-3 py-2 rounded-lg text-xs ${status.includes("Error") || status.includes("Failed") ? "bg-red-50 text-red-600 border border-red-200" : "bg-green-50 text-green-600 border border-green-200"}`}>
          {status}
        </div>
      )}
      <p className="text-[11px] text-gray-400">API keys stored in AWS Secrets Manager. Values are never shown after saving.</p>
      {secrets.map((s, idx) => (
        <div key={idx} className="flex gap-2 items-center">
          <input type="text" value={s.key} onChange={(e) => updateRow(idx, "key", e.target.value)}
            placeholder="KEY_NAME" className={inputClass + " flex-1 font-mono"} />
          <input type={showValues ? "text" : "password"} value={s.value} onChange={(e) => updateRow(idx, "value", e.target.value)}
            placeholder="value" className={inputClass + " flex-1"} />
          <button onClick={() => removeRow(idx)} className="p-1.5 text-gray-300 hover:text-red-500 transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
        </div>
      ))}
      <div className="flex items-center gap-3 mt-1">
        <button onClick={addRow} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium">
          <Plus className="w-3.5 h-3.5" /> Add secret
        </button>
        {secrets.length > 0 && (
          <>
            <button onClick={() => setShowValues(!showValues)} className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
              {showValues ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />} {showValues ? "Hide" : "Show"}
            </button>
            <button onClick={saveSecrets} disabled={saving}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 ml-auto font-medium shadow-sm">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save secrets
            </button>
          </>
        )}
      </div>
    </Section>
  );
}
