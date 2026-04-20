import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate, useSearchParams, useBlocker } from "react-router";
import { useTranslation } from "react-i18next";
import { Loader2, Code2, GitCompare, X, Sparkles } from "lucide-react";
import { DiffEditor } from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { useToolLibraryStore, type ToolTemplate } from "../../stores/tool-library-store";
import { useToolAssistantStore } from "../../stores/tool-assistant-store";
import { useUISettings } from "../../stores/ui-settings-store";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { preloadPyodide, isPyodideReady, checkPythonSyntax } from "../../lib/pyodide-checker";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { publishTool, unpublishTool } from "../../lib/api-client";
import { getCurrentUser } from "aws-amplify/auth";
import ToolAssistant from "../tools/ToolAssistant";
import useIsDark from "../../hooks/useIsDark";
import type { ValidationResult } from "../../lib/types/validation";
import { validatePython } from "../../lib/validators/python-validator";
import ConfirmDialog from "../ui/ConfirmDialog";
import PublishToggle from "../shared/PublishToggle";
import ToolToolbar from "./ToolToolbar";
import ToolEditorPane from "./ToolEditorPane";

const TOOL_TEMPLATE = `@tool
def my_tool(query: str) -> str:
    """Tool description.

    Args:
        query: The input parameter.

    Returns:
        Result as string.
    """
    return "result"
`;

function extractFuncName(code: string): string | null {
  const match = code.match(/@tool\s*\ndef\s+(\w+)\s*\(/);
  return match ? match[1] : null;
}

export default function ToolDetail() {
  const { t } = useTranslation();
  const { toolId } = useParams<{ toolId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const isDark = useIsDark();

  const { tools, fetchTools, saveTool, softDeleteTool, saving, error, clearError } = useToolLibraryStore();
  const { panelOpen, openPanel } = useToolAssistantStore();

  const isNew = searchParams.get("new") === "1";
  const paramName = searchParams.get("name") || "";
  const paramDesc = searchParams.get("desc") || "";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [originalName, setOriginalName] = useState("");
  const [originalDescription, setOriginalDescription] = useState("");
  const [code, setCode] = useState("");
  const [originalCode, setOriginalCode] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [fetchAttempted, setFetchAttempted] = useState(false);
  const [currentUser, setCurrentUser] = useState("");
  const [toolOwner, setToolOwner] = useState("");
  const [visibility, setVisibility] = useState<string>("private");
  const { currentWorkspace } = useWorkspaceStore();

  const editorRef = useRef<MonacoNS.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof MonacoNS | null>(null);
  const validateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef<(() => void) | undefined>(undefined);

  useEffect(() => { getCurrentUser().then(u => setCurrentUser(u.username)).catch(() => {}); }, []);

  // ESC to close diff
  useEffect(() => {
    if (!showDiff) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setShowDiff(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showDiff]);

  // Load tool data
  useEffect(() => {
    if (tools.length === 0 && !fetchAttempted) { setFetchAttempted(true); fetchTools(); return; }
    if (isNew) {
      const existing = tools.find((t) => t.id === toolId);
      if (existing) { navigate(`/tools/${toolId}`, { replace: true }); return; }
      setName(paramName); setDescription(paramDesc); setOriginalName(paramName); setOriginalDescription(paramDesc);
      setCode(TOOL_TEMPLATE); setOriginalCode(""); setToolOwner(""); setVisibility("private"); setLoaded(true);
      if (toolId) openPanel(toolId);
      return;
    }
    const tool = tools.find((t) => t.id === toolId);
    if (!tool) { setNotFound(true); setLoaded(true); return; }
    setName(tool.name); setDescription(tool.description); setOriginalName(tool.name); setOriginalDescription(tool.description);
    setCode(tool.code); setOriginalCode(tool.code); setToolOwner(tool.owner); setVisibility(tool.visibility || "private"); setLoaded(true);
    if (toolId) openPanel(toolId);
  }, [tools, toolId, isNew, paramName, paramDesc, fetchTools]);

  const hasChanges = code !== originalCode || name !== originalName || description !== originalDescription;
  const canEdit = isNew || (currentUser && toolOwner === currentUser) || toolOwner === "__builtin__";

  useEffect(() => { preloadPyodide(); }, []);

  // Ctrl+S
  useEffect(() => { saveRef.current = handleSave; });
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveRef.current?.(); } };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // beforeunload
  useEffect(() => {
    if (!hasChanges) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [hasChanges]);

  const blocker = useBlocker(hasChanges);

  const runValidation = useCallback((value: string) => {
    if (!monacoRef.current || !editorRef.current) return;
    const monaco = monacoRef.current;
    const model = editorRef.current.getModel();
    if (!model) return;
    const markers: MonacoNS.editor.IMarkerData[] = [];
    for (const err of validatePython(value)) {
      markers.push({ severity: err.severity as MonacoNS.MarkerSeverity, message: err.message, startLineNumber: err.line, startColumn: err.col, endLineNumber: err.line, endColumn: err.col + 1 });
    }
    if (isPyodideReady()) {
      for (const err of checkPythonSyntax(value)) {
        markers.push({ severity: monaco.MarkerSeverity.Error, message: err.msg, startLineNumber: err.line, startColumn: err.col || 1, endLineNumber: err.line, endColumn: (err.col || 1) + 1 });
      }
    }
    monaco.editor.setModelMarkers(model, "tool-validator", markers);
  }, []);

  const handleCodeChange = useCallback((value: string | undefined) => {
    const v = value || "";
    setCode(v);
    if (validateTimer.current) clearTimeout(validateTimer.current);
    validateTimer.current = setTimeout(() => runValidation(v), 500);
  }, [runValidation]);

  const handleCodeUpdate = useCallback((newCode: string) => {
    setCode(newCode);
    if (validateTimer.current) clearTimeout(validateTimer.current);
    validateTimer.current = setTimeout(() => runValidation(newCode), 300);
  }, [runValidation]);

  const handleValidate = async () => {
    setValidating(true);
    const errors: string[] = [];
    const warnings: string[] = [];
    for (const err of validatePython(code)) {
      if (err.severity >= 8) errors.push(`Line ${err.line}: ${err.message}`);
      else warnings.push(`Line ${err.line}: ${err.message}`);
    }
    if (isPyodideReady()) for (const err of checkPythonSyntax(code)) errors.push(`Line ${err.line}: ${err.msg}`);
    const funcName = extractFuncName(code);
    if (!funcName) errors.push(t("tools.missingDecorator"));
    if (funcName && !code.includes('"""')) warnings.push(t("tools.missingDocstring"));
    if (funcName && !code.match(/def\s+\w+\([^)]*\)\s*->\s*str/)) warnings.push(t("tools.missingReturnType"));

    if (errors.length === 0) {
      try {
        const lang = useUISettings.getState().language;
        const langHint = lang === "zh" ? "用中文回复。" : "Respond in English.";
        const validatePrompt = `${langHint}\nYou are a code reviewer for Agent Studio @tool functions. Review this tool and report ONLY issues that affect functionality, correctness, or user experience.\n\n## What to Report as Errors\n- Syntax errors or runtime errors\n- Missing @tool decorator\n- Missing or incorrect type hints that would cause runtime failures\n- Logic bugs that produce wrong results\n- Security issues (injection, data leaks)\n\n## What to Report as Warnings\n- Missing docstring or incomplete Args/Returns documentation\n- No input validation for user-provided data\n- No error handling for operations that can fail\n- Hardcoded values that should be parameters\n\n## What to IGNORE\n- Code style preferences\n- Minor refactoring suggestions\n- Performance micro-optimizations\n\nCode:\n\`\`\`python\n${code}\n\`\`\`\n\nRespond with ONLY a JSON block:\n\`\`\`json\n{"valid": true/false, "errors": ["..."], "warnings": ["..."]}\n\`\`\``;
        let result = "";
        for await (const chunk of invokeMetaAgent(validatePrompt, [])) {
          const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
          if (cleaned) result += cleaned;
        }
        const jsonMatch = result.match(/\{[\s\S]*"valid"[\s\S]*\}/);
        if (jsonMatch) {
          try { const parsed = JSON.parse(jsonMatch[0]); if (Array.isArray(parsed.errors)) errors.push(...parsed.errors); if (Array.isArray(parsed.warnings)) warnings.push(...parsed.warnings); } catch { /* skip */ }
        }
      } catch { /* skip */ }
    }

    if (errors.length === 0 && warnings.length === 0) {
      setValidationResult({ valid: true, errors: [], warnings: [] });
      setTimeout(() => setValidationResult(null), 2000);
    } else {
      setValidationResult({ valid: errors.length === 0, errors, warnings });
    }
    setValidating(false);
  };

  const handleSave = async () => {
    clearError();
    const funcName = extractFuncName(code);
    if (!funcName) { setValidationError(t("tools.missingDecorator")); return; }
    const existing = tools.find((t) => t.id === funcName);
    if (existing && funcName !== toolId) { setValidationError(t("tools.nameConflict")); return; }
    setValidationError(null);
    const tool: ToolTemplate = { id: funcName, name: name || funcName, description, category: "custom", code, builtin: false, owner: "", visibility: "shared", created_at: "", updated_at: "" };
    try {
      await saveTool(tool);
      setOriginalCode(code); setOriginalName(name || funcName); setOriginalDescription(description);
      if (isNew || funcName !== toolId) navigate(`/tools/${funcName}`, { replace: true });
    } catch { /* error in store */ }
  };

  const handleDelete = async () => {
    if (!toolId) return;
    if (isNew || !originalCode) { navigate("/tools"); return; }
    try { await softDeleteTool(toolId); navigate("/tools"); } catch { /* error in store */ }
  };

  const handleDiscard = () => { setCode(originalCode); setName(originalName); setDescription(originalDescription); setShowDiff(false); };

  if (!loaded) return <div className="flex items-center justify-center h-full"><Loader2 className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500" /></div>;
  if (notFound) return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500">
      <Code2 className="w-12 h-12 mb-3 opacity-30" />
      <p className="text-sm font-medium">{t("tools.toolNotFound")}</p>
      <button onClick={() => navigate("/tools")} className="mt-3 text-xs text-blue-500 hover:underline">{t("common.back")}</button>
    </div>
  );

  return (
    <div className="flex flex-col h-full">
      <ToolToolbar
        toolName={name || toolId || ""}
        description={description}
        hasChanges={hasChanges}
        saving={saving}
        validating={validating}
        assistantOpen={panelOpen}
        onBack={() => navigate("/tools")}
        onSave={handleSave}
        onDiscard={handleDiscard}
        onValidate={handleValidate}
        onShowDiff={() => setShowDiff(true)}
        onDelete={() => setConfirmDelete(true)}
        onToggleAssistant={() => { if (toolId) { if (panelOpen) useToolAssistantStore.getState().closePanel(); else openPanel(toolId); } }}
        extraSlot={!isNew && toolId && !hasChanges && toolOwner !== "__builtin__" ? (
          <PublishToggle
            visibility={visibility}
            canPublish={(currentWorkspace?.role === "admin" || currentWorkspace?.role === "owner")}
            onPublish={async () => { await publishTool(toolId); }}
            onUnpublish={async () => { await unpublishTool(toolId); }}
            onChange={(v) => setVisibility(v)}
            testId="tool-publish-toggle"
          />
        ) : null}
      />

      {/* Validation results */}
      {validationResult && (
        <div className={`mx-6 mt-2 rounded-lg text-sm border ${!validationResult.valid ? isDark ? "bg-red-900/20 border-red-800" : "bg-red-50 border-red-200" : validationResult.errors.length === 0 && validationResult.warnings.length === 0 ? isDark ? "bg-green-900/20 border-green-800" : "bg-green-50 border-green-200" : isDark ? "bg-amber-900/20 border-amber-800" : "bg-amber-50 border-amber-200"}`}>
          <div className="px-4 py-2">
            <div className="flex items-center justify-between">
              <p className={`text-xs font-medium ${!validationResult.valid ? "text-red-500" : validationResult.errors.length === 0 && validationResult.warnings.length === 0 ? isDark ? "text-green-400" : "text-green-600" : "text-amber-600"}`}>
                {!validationResult.valid ? t("validation.failed") : validationResult.errors.length === 0 && validationResult.warnings.length === 0 ? t("validation.noIssues") : t("validation.passedWithWarnings", { count: validationResult.warnings.length })}
              </p>
              <button onClick={() => setValidationResult(null)} className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400"><X className="w-3 h-3" /></button>
            </div>
            {validationResult.errors.map((e, i) => <p key={`e${i}`} className="text-[11px] text-red-500 mt-1">&#x2716; {e}</p>)}
            {validationResult.warnings.map((w, i) => <p key={`w${i}`} className="text-[11px] text-amber-600 mt-1">&#x26A0; {w}</p>)}
            {canEdit && (validationResult.errors.length > 0 || validationResult.warnings.length > 0) && (
              <button onClick={() => {
                const issues = [...validationResult.errors.map(e => `Error: ${e}`), ...validationResult.warnings.map(w => `Warning: ${w}`)].join("\n");
                if (toolId) {
                  openPanel(toolId);
                  setTimeout(() => {
                    useToolAssistantStore.getState().sendMessage(
                      `## Auto-Fix Task\nFix ONLY the following validation issues. Do NOT remove or rewrite any existing content.\n\nIssues:\n${issues}\n\nRules:\n- Use __tool_code (4 backticks) to output the COMPLETE fixed tool function.\n- Fix ONLY the specific issues listed above.\n- NEVER delete existing content, sections, or descriptions.\n- NEVER shorten or summarize existing text.\n- If an issue appears already fixed in the current code, skip it and say so.\n- Do NOT ask for confirmation. Execute fixes immediately.`,
                      { name, description, category: "custom", code }, handleCodeUpdate,
                    );
                  }, 100);
                }
                setValidationResult(null);
              }} className={`mt-2 flex items-center gap-1 px-2 py-1 text-[11px] rounded transition-colors ${isDark ? "text-blue-400 hover:bg-blue-900/30" : "text-blue-600 hover:bg-blue-50"}`}>
                <Sparkles className="w-3 h-3" /> {t("common.autoFix")}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <ToolEditorPane
          name={name} description={description} code={code}
          onNameChange={setName} onDescriptionChange={setDescription} onCodeChange={handleCodeChange}
          editorRef={editorRef} monacoRef={monacoRef}
          errorBar={(error || validationError) ? (
            <div className={`px-4 py-2 text-xs flex items-center justify-between ${isDark ? "bg-red-900/20 border-t border-red-800 text-red-400" : "bg-red-50 border-t border-red-200 text-red-600"}`}>
              <span>{validationError || error}</span>
              <button onClick={() => { setValidationError(null); clearError(); }} className="text-red-400 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 text-[10px]">{t("common.dismiss")}</button>
            </div>
          ) : null}
        />
        {panelOpen && toolId && (
          <ToolAssistant toolId={toolId} currentCode={code} toolName={name} toolDescription={description} toolCategory="custom" onCodeUpdate={handleCodeUpdate} />
        )}
      </div>

      {/* Diff modal */}
      {showDiff && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center" onClick={() => setShowDiff(false)}>
          <div className={`w-[90vw] h-[80vh] rounded-xl shadow-2xl flex flex-col overflow-hidden ${isDark ? "bg-gray-900" : "bg-white"}`} onClick={(e) => e.stopPropagation()}>
            <div className={`flex items-center justify-between px-4 py-2 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
              <div className="flex items-center gap-2">
                <GitCompare className={`w-4 h-4 ${isDark ? "text-blue-400" : "text-blue-500"}`} />
                <span className={`text-sm font-medium ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.changes")}</span>
                <span className={`text-xs px-2 py-0.5 rounded-full font-mono ${isDark ? "bg-blue-900/40 text-blue-300" : "bg-blue-100 text-blue-700"}`}>{extractFuncName(code) || toolId}</span>
              </div>
              <button onClick={() => setShowDiff(false)} className={`text-[12px] px-2 py-1 rounded ${isDark ? "text-gray-400 hover:text-gray-200 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}>{t("common.close")}</button>
            </div>
            <div className="flex-1 min-h-0">
              <DiffEditor original={originalCode} modified={code} language="python" theme={isDark ? "vs-dark" : "light"} keepCurrentOriginalModel keepCurrentModifiedModel options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, renderSideBySide: true }} />
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={t("skillEditor.moveToTrash")}
        message={t("tools.deleteConfirm", { name: name || toolId })}
        confirmLabel={t("skillEditor.moveToTrashBtn")}
        cancelLabel={t("common.cancel")}
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(false)}
      />

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title={t("skillEditor.unsavedChanges")}
        message={t("skillEditor.unsavedDesc", { count: 1 })}
        confirmLabel={t("skillEditor.discardLeave")}
        cancelLabel={t("skillEditor.stay")}
        danger
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
    </div>
  );
}
