import { useState, useEffect } from "react";
import { updateAgent, apiPut, putStorage, getDownloadUrl } from "../lib/api-client";
import { invokeMetaAgent } from "../lib/agentcore-client";
import { useEditAssistantStore } from "../stores/edit-assistant-store";
import { useTranslation } from "react-i18next";
import type { AgentMetadata } from "../lib/agent-metadata";

/** Validation result with optional prompt quality scores */
export interface DeployValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  prompt_scores?: Record<string, number>;
  prompt_overall?: number;
}

export interface UseAgentDeployParams {
  agentId: string | null;
  agentName: string | null;
  formData: Partial<AgentMetadata> | null;
  isCreateMode: boolean;
  updateField: <K extends keyof AgentMetadata>(key: K, value: AgentMetadata[K]) => void;
  setSaving: (saving: boolean) => void;
  markSaved: () => void;
  fetchAgents: () => void;
  onNavigateBack: () => void;
}

export interface AgentDeployState {
  status: string | null;
  errorDetail: string | null;
  savingDraft: boolean;
  progressStep: string | null;
  progressPct: number;
  showReview: false | "view" | "deploy";
  setShowReview: (v: false | "view" | "deploy") => void;
  validationResult: DeployValidationResult | null;
  validating: boolean;
  pendingStagingKey: string | null;
  autoFixing: boolean;
  previewCode: string | null;
  setPreviewCode: (v: string | null) => void;
  previewLoading: boolean;
  handleValidateOnly: () => Promise<void>;
  handleSave: () => Promise<void>;
  doDeploy: (stagingKey: string) => Promise<void>;
  handleAutoFix: () => Promise<void>;
  handleSaveDraft: () => Promise<void>;
  handleOptimizeField: (fieldName: string, fieldLabel: string) => void;
  handlePreviewCode: () => Promise<void>;
  dismissValidation: () => void;
}

// ---------------------------------------------------------------------------
// Helpers (pure functions, no React state)
// ---------------------------------------------------------------------------

/** Extract structured tool results from a Meta-Agent stream response. */
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

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAgentDeploy(params: UseAgentDeployParams): AgentDeployState {
  const {
    agentId, agentName, formData, isCreateMode,
    updateField, setSaving, markSaved, fetchAgents, onNavigateBack,
  } = params;

  const { t } = useTranslation();

  const [status, setStatus] = useState<string | null>(null);
  const [errorDetail, setErrorDetail] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);
  const [progressStep, setProgressStep] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [showReview, setShowReview] = useState<false | "view" | "deploy">(false);
  const [validationResult, setValidationResult] = useState<DeployValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [pendingStagingKey, setPendingStagingKey] = useState<string | null>(null);
  const [autoFixing, setAutoFixing] = useState(false);
  const [previewCode, setPreviewCode] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Reset deploy state when switching agents
  useEffect(() => {
    setStatus(null);
    setErrorDetail(null);
    setProgressStep(null);
    setProgressPct(0);
    setValidationResult(null);
    setValidating(false);
    setPendingStagingKey(null);
    setPreviewCode(null);
  }, [agentId]);

  // ---- Validate only ----
  const handleValidateOnly = async () => {
    setValidating(true);
    setStatus(null);
    setValidationResult(null);

    try {
      const stagingData = {
        name: formData?.name || agentName,
        display_name: formData?.display_name || agentName,
        description: formData?.description || "",
        system_prompt: formData?.system_prompt || "",
        tool_definitions: formData?.tool_definitions || "",
        tool_names: formData?.tool_names || "",
        template_id: formData?.template_id || "",
        supports_images: formData?.supports_images || false,
        skills: formData?.skills || [],
        agent_id: agentId,
      };

      let stagingKey: string;
      try {
        if (agentId && !isCreateMode) {
          await apiPut(`/agents/${agentId}/files?path=staging.json`, { content: JSON.stringify(stagingData) });
          stagingKey = `agents/${agentId}/staging.json`;
        } else {
          const draftKey = `staging/${agentId || "new"}.json`;
          await putStorage(draftKey, stagingData);
          stagingKey = draftKey;
        }
      } catch {
        setStatus("Failed to upload config");
        return;
      }

      const { toolResults } = await streamMetaAgent(
        `Execute validate_agent with staging_key: ${stagingKey}\nDo NOT ask for confirmation.`,
      );
      const validation = toolResults.validate_agent as DeployValidationResult | undefined;

      if (validation) {
        setValidationResult(validation);
        setPendingStagingKey(stagingKey);
        if (validation.valid && validation.warnings.length === 0) {
          setStatus(t("agentEditor.validationPassed"));
        }
      } else {
        setStatus(t("agentEditor.validationNoResult"));
      }
    } catch (err) {
      setStatus(t("agentEditor.validationError", { error: err instanceof Error ? err.message : "Unknown" }));
    } finally {
      setValidating(false);
    }
  };

  // ---- Full save (validate + deploy) ----
  const handleSave = async () => {
    setSaving(true);
    setStatus(null);
    setErrorDetail(null);
    setValidationResult(null);

    try {
      const stagingData = {
        name: formData?.name || agentName,
        display_name: formData?.display_name || agentName,
        description: formData?.description || "",
        system_prompt: formData?.system_prompt || "",
        tool_definitions: formData?.tool_definitions || "",
        tool_names: formData?.tool_names || "",
        welcome_message: formData?.welcome_message || "",
        suggestions: Array.isArray(formData?.suggestions) ? formData.suggestions : (formData?.suggestions || "").split("|").filter(Boolean),
        template_id: formData?.template_id || "",
        supports_images: formData?.supports_images || false,
        skills: formData?.skills || [],
        agent_id: agentId,
      };

      let stagingKey: string;
      try {
        if (agentId && !isCreateMode) {
          await apiPut(`/agents/${agentId}/files?path=staging.json`, { content: JSON.stringify(stagingData) });
          stagingKey = `agents/${agentId}/staging.json`;
        } else {
          const draftKey = `staging/${agentId || "new"}.json`;
          await putStorage(draftKey, stagingData);
          stagingKey = draftKey;
        }
      } catch {
        setStatus("Failed to upload config to S3");
        setSaving(false);
        return;
      }

      // Step 1: Validate
      setProgressStep(t("agentEditor.validating"));
      setProgressPct(10);
      const valPrompt = `Execute validate_agent with staging_key: ${stagingKey}\nDo NOT ask for confirmation.`;
      const { toolResults: valToolResults } = await streamMetaAgent(valPrompt);
      setValidating(false);
      setProgressPct(30);

      const validation = valToolResults.validate_agent as DeployValidationResult | undefined;

      if (validation) {
        setValidationResult(validation);
        setPendingStagingKey(stagingKey);
        if (!validation.valid) {
          setProgressStep(null);
          setStatus(t("agentEditor.validationFailed"));
          setSaving(false);
          return;
        }
        if (validation.warnings.length > 0) {
          setProgressStep(null);
          setStatus(null);
          setSaving(false);
          return;
        }
      }

      // Step 2: Deploy
      await doDeploy(stagingKey);
    } catch (err) {
      setProgressStep(null);
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setSaving(false);
      setValidating(false);
    }
  };

  // ---- Deploy ----
  const doDeploy = async (stagingKey: string) => {
    setSaving(true);
    setStatus(null);
    setErrorDetail(null);
    setProgressStep(isCreateMode ? t("agentEditor.preparing") : t("agentEditor.updating"));

    try {
      const prompt = isCreateMode
        ? `Execute create_agent with staging_key: ${stagingKey}
The full config is in S3. Read it and use those parameters.
- agent_name: ${formData?.name || agentName}
- permission_tier: readonly

Do NOT ask for confirmation. Execute create_agent immediately.`
        : `Execute update_agent with these parameters:
- agent_id: ${agentId}
- staging_key: ${stagingKey}

The full config (system_prompt, tool_definitions, etc.) is in the S3 staging file. Pass staging_key to update_agent.
Do NOT ask for confirmation. Execute update_agent immediately.`;

      const deployToolNameMap: Record<string, string> = {
        create_agent: t("agentEditor.creatingAgent"),
        update_agent: t("agentEditor.updatingAgent"),
        upload_deployment: t("agentEditor.uploadingCode"),
        deploy_agent: t("agentEditor.deploying"),
        get_agent_status: t("agentEditor.checkingStatus"),
        save_metadata: t("agentEditor.savingMetadata"),
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

      const deployResult = (toolResults.update_agent || toolResults.create_agent) as { error?: string; status?: string; details?: string[] } | undefined;
      const failed = deployResult?.error != null;

      setProgressStep(null);
      setProgressPct(failed ? 0 : 100);
      setStatus(failed ? (isCreateMode ? t("agentEditor.createFailed") : t("agentEditor.updateFailed")) : (isCreateMode ? t("agentEditor.createSuccess") : t("agentEditor.updateSuccess")));

      if (failed) {
        const errorMsg = deployResult!.error!;
        const details = deployResult!.details?.join("\n") || "";
        setErrorDetail(details || errorMsg);
        setValidationResult({ valid: false, errors: [errorMsg, ...(deployResult!.details || [])], warnings: [] });
        setPendingStagingKey(stagingKey);
      } else {
        setErrorDetail(null);
      }
      fetchAgents();

      if (!failed) {
        markSaved();
        if (agentId && formData?.tool_definitions) {
          updateAgent(agentId, { ...formData, agent_id: agentId, expected_updated_at: formData.updated_at }).catch(() => {});
        }
        if (isCreateMode) onNavigateBack();
      }
    } catch (err) {
      setProgressStep(null);
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  // ---- Auto-fix ----
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

    // 2. Pass ALL validation issues to AI assistant
    const allIssues = [
      ...validationResult.errors.map(e => `ERROR: ${e}`),
      ...validationResult.warnings.map(w => `WARNING: ${w}`),
    ];

    if (allIssues.length > 0) {
      const { sendMessage, openPanel } = useEditAssistantStore.getState();
      openPanel(agentId!);
      const fixPrompt = `## Auto-Fix Task
Fix ONLY the following validation issues. Do NOT remove or rewrite any existing content.

Issues:
${allIssues.map((issue, i) => `${i + 1}. ${issue}`).join("\n")}

Rules:
- For small changes (< 30% of field): use __field_edit (search/replace) with small precise SEARCH blocks.
- For large changes (> 30% of field, e.g. translating, restructuring): use __update with the COMPLETE new value.
- Use __update JSON for short fields (description, etc.).
- Do NOT set tool_names — it is auto-computed.
- Fix ONLY the specific issues listed above.
- NEVER delete existing content, sections, or descriptions.
- NEVER shorten or summarize existing text.
- Make minimal, surgical changes.
- If an issue appears already fixed in the current content, skip it and say so.
- If SEARCH text cannot be found, the issue may have been fixed already — do NOT attempt alternative fixes.
- Do NOT ask for confirmation. Execute fixes immediately.`;

      await sendMessage(fixPrompt, { ...formData }, (updates) => {
        for (const [key, value] of Object.entries(updates)) {
          if (key === "tool_definitions" && typeof value === "string" && formData.tool_definitions) {
            const existingBlocks = (formData.tool_definitions).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
            const newBlocks = (value as string).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
            const merged = new Map<string, string>();
            for (const b of existingBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0, 30); merged.set(n, b); }
            for (const b of newBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0, 30); merged.set(n, b); }
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

  // ---- Optimize field via AI ----
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

  // ---- Save draft ----
  const handleSaveDraft = async () => {
    setSavingDraft(true);
    setStatus(null);
    try {
      const draftData = {
        ...formData,
        _draftSavedAt: new Date().toISOString(),
        _agentId: agentId,
        _agentName: agentName,
      };
      if (isCreateMode) {
        await putStorage(`drafts/${formData?.name || "untitled"}.json`, draftData);
      } else {
        await apiPut(
          `/agents/${agentId}/files?path=draft.json`,
          { content: JSON.stringify(draftData) }
        );
      }
      setStatus(t("agentEditor.draftSaved"));
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setSavingDraft(false);
    }
  };

  // ---- Preview assembled code ----
  const handlePreviewCode = async () => {
    if (!pendingStagingKey) return;
    setPreviewLoading(true);
    try {
      const prompt = `Execute preview_assembled_code with staging_key: ${pendingStagingKey}\nReturn ONLY the JSON result. Do NOT include the code in your response.`;
      let result = "";
      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
      for await (const chunk of stream) { result += chunk; }
      const clean = result.replace(/\{"__tool"[^}]*\}/g, "");
      const keyMatch = clean.match(/"preview_key"\s*:\s*"([^"]+)"/);
      if (keyMatch) {
        const presignedUrl = await getDownloadUrl(keyMatch[1]);
        if (presignedUrl) {
          const resp = await fetch(presignedUrl);
          if (resp.ok) {
            const buf = await resp.arrayBuffer();
            setPreviewCode(new TextDecoder().decode(buf));
          }
        }
      }
    } finally {
      setPreviewLoading(false);
    }
  };

  return {
    status, errorDetail, savingDraft, progressStep, progressPct,
    showReview, setShowReview,
    validationResult, validating, pendingStagingKey, autoFixing,
    previewCode, setPreviewCode, previewLoading,
    handleValidateOnly, handleSave, doDeploy, handleAutoFix,
    handleSaveDraft, handleOptimizeField, handlePreviewCode,
    dismissValidation: () => { setValidationResult(null); setPendingStagingKey(null); },
  };
}
