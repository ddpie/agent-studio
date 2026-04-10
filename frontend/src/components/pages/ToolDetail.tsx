import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate, useSearchParams, useBlocker } from "react-router";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft, Loader2, Save, Trash2, GitCompare, Sparkles, Code2, ShieldCheck, X,
} from "lucide-react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { useToolLibraryStore, type ToolTemplate } from "../../stores/tool-library-store";
import { useToolAssistantStore } from "../../stores/tool-assistant-store";
import { useUISettings } from "../../stores/ui-settings-store";
import { preloadPyodide, isPyodideReady, checkPythonSyntax } from "../../lib/pyodide-checker";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { getCurrentUser } from "aws-amplify/auth";
import ToolAssistant from "../tools/ToolAssistant";
import useIsDark from "../../hooks/useIsDark";
import type { ValidationResult } from "../../lib/types/validation";
import { validatePython } from "../../lib/validators/python-validator";

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

  // ESC to close diff modal
  useEffect(() => {
    if (!showDiff) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setShowDiff(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showDiff]);
  const [loaded, setLoaded] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [fetchAttempted, setFetchAttempted] = useState(false);
  const [currentUser, setCurrentUser] = useState("");
  const [toolOwner, setToolOwner] = useState("");

  const editorRef = useRef<MonacoNS.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof MonacoNS | null>(null);
  const validateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef<(() => void) | undefined>(undefined);

  // Load current user
  useEffect(() => { getCurrentUser().then(u => setCurrentUser(u.username)).catch(() => {}); }, []);

  // Load tool data
  useEffect(() => {
    if (tools.length === 0 && !fetchAttempted) {
      setFetchAttempted(true);
      fetchTools();
      return;
    }

    if (isNew) {
      const existing = tools.find((t) => t.id === toolId);
      if (existing) {
        navigate(`/tools/${toolId}`, { replace: true });
        return;
      }
      setName(paramName);
      setDescription(paramDesc);
      setOriginalName(paramName);
      setOriginalDescription(paramDesc);
      setCode(TOOL_TEMPLATE);
      setOriginalCode("");
      setToolOwner("");
      setLoaded(true);
      if (toolId) openPanel(toolId);
      return;
    }

    const tool = tools.find((t) => t.id === toolId);
    if (!tool) {
      setNotFound(true);
      setLoaded(true);
      return;
    }

    setName(tool.name);
    setDescription(tool.description);
    setOriginalName(tool.name);
    setOriginalDescription(tool.description);
    setCode(tool.code);
    setOriginalCode(tool.code);
    setToolOwner(tool.owner);
    setLoaded(true);
    if (toolId) openPanel(toolId);
  }, [tools, toolId, isNew, paramName, paramDesc, fetchTools]);

  const hasChanges = code !== originalCode || name !== originalName || description !== originalDescription;

  // Permission: can edit if mine, seed, or new
  const isMine = currentUser && toolOwner === currentUser;
  const isSeed = toolOwner === "__builtin__";
  const canEdit = isNew || isMine || isSeed;

  useEffect(() => { preloadPyodide(); }, []);

  // Ctrl+S
  useEffect(() => { saveRef.current = handleSave; });
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        saveRef.current?.();
      }
    };
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

  // Monaco validation
  const runValidation = useCallback((value: string) => {
    if (!monacoRef.current || !editorRef.current) return;
    const monaco = monacoRef.current;
    const model = editorRef.current.getModel();
    if (!model) return;

    const markers: MonacoNS.editor.IMarkerData[] = [];
    for (const err of validatePython(value)) {
      markers.push({
        severity: err.severity as MonacoNS.MarkerSeverity,
        message: err.message,
        startLineNumber: err.line, startColumn: err.col,
        endLineNumber: err.line, endColumn: err.col + 1,
      });
    }
    if (isPyodideReady()) {
      for (const err of checkPythonSyntax(value)) {
        markers.push({
          severity: monaco.MarkerSeverity.Error,
          message: err.msg,
          startLineNumber: err.line, startColumn: err.col || 1,
          endLineNumber: err.line, endColumn: (err.col || 1) + 1,
        });
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

  const handleEditorMount = useCallback((editor: MonacoNS.editor.IStandaloneCodeEditor, monaco: typeof MonacoNS) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    setTimeout(() => runValidation(code), 300);
  }, [runValidation, code]);

  // Validate button handler
  const handleValidate = async () => {
    setValidating(true);
    const errors: string[] = [];
    const warnings: string[] = [];

    // Structural checks
    for (const err of validatePython(code)) {
      if (err.severity >= 8) errors.push(`Line ${err.line}: ${err.message}`);
      else warnings.push(`Line ${err.line}: ${err.message}`);
    }

    // Pyodide compile check
    if (isPyodideReady()) {
      for (const err of checkPythonSyntax(code)) {
        errors.push(`Line ${err.line}: ${err.msg}`);
      }
    }

    // Check @tool function name
    const funcName = extractFuncName(code);
    if (!funcName) {
      errors.push(t("tools.missingDecorator"));
    }

    // Check docstring
    if (funcName && !code.includes('"""')) {
      warnings.push(t("tools.missingDocstring"));
    }

    // Check return type hint
    if (funcName && !code.match(/def\s+\w+\([^)]*\)\s*->\s*str/)) {
      warnings.push(t("tools.missingReturnType"));
    }

    // AI quality check (only if local checks pass)
    if (errors.length === 0) {
      try {
        const lang = useUISettings.getState().language;
        const langHint = lang === "zh" ? "用中文回复。" : "Respond in English.";
        const validatePrompt = `${langHint}
You are a code reviewer for Agent Studio @tool functions. Review this tool and report ONLY issues that affect functionality, correctness, or user experience.

## What to Report as Errors
- Syntax errors or runtime errors
- Missing @tool decorator
- Missing or incorrect type hints that would cause runtime failures
- Logic bugs that produce wrong results
- Security issues (injection, data leaks)

## What to Report as Warnings
- Missing docstring or incomplete Args/Returns documentation
- No input validation for user-provided data (empty strings, wrong types)
- No error handling for operations that can fail (network, file I/O, parsing)
- Hardcoded values that should be parameters

## What to IGNORE (do NOT report)
- Code style preferences (import order, variable naming conventions)
- Minor refactoring suggestions (extract helper, move function)
- Performance micro-optimizations
- "Could be improved" suggestions without concrete impact

Code:
\`\`\`python
${code}
\`\`\`

Respond with ONLY a JSON block:
\`\`\`json
{"valid": true/false, "errors": ["..."], "warnings": ["..."]}
\`\`\``;
        let result = "";
        for await (const chunk of invokeMetaAgent(validatePrompt, [])) {
          const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
          if (cleaned) result += cleaned;
        }
        const jsonMatch = result.match(/\{[\s\S]*"valid"[\s\S]*\}/);
        if (jsonMatch) {
          try {
            const parsed = JSON.parse(jsonMatch[0]);
            if (Array.isArray(parsed.errors)) errors.push(...parsed.errors);
            if (Array.isArray(parsed.warnings)) warnings.push(...parsed.warnings);
          } catch { /* JSON parse failed */ }
        }
      } catch { /* Meta-Agent call failed */ }
    }

    if (errors.length === 0 && warnings.length === 0) {
      setValidationResult({ valid: true, errors: [], warnings: [] });
      setTimeout(() => setValidationResult(null), 2000);
    } else {
      setValidationResult({ valid: errors.length === 0, errors, warnings });
    }
    setValidating(false);
  };

  // Save
  const handleSave = async () => {
    clearError();
    const funcName = extractFuncName(code);
    if (!funcName) {
      setValidationError(t("tools.missingDecorator"));
      return;
    }
    const existing = tools.find((t) => t.id === funcName);
    if (existing && funcName !== toolId) {
      setValidationError(t("tools.nameConflict"));
      return;
    }
    setValidationError(null);

    const tool: ToolTemplate = {
      id: funcName,
      name: name || funcName,
      description,
      category: "custom",
      code,
      builtin: false,
      owner: "",
      visibility: "shared",
      created_at: "",
      updated_at: "",
    };

    try {
      await saveTool(tool);
      setOriginalCode(code);
      setOriginalName(name || funcName);
      setOriginalDescription(description);
      if (isNew || funcName !== toolId) {
        navigate(`/tools/${funcName}`, { replace: true });
      }
    } catch {
      // error is set in store
    }
  };

  // Delete
  const handleDelete = async () => {
    if (!toolId) return;
    if (isNew || !originalCode) {
      navigate("/tools");
      return;
    }
    try {
      await softDeleteTool(toolId);
      navigate("/tools");
    } catch {
      // error is set in store
    }
  };

  // Discard
  const handleDiscard = () => {
    setCode(originalCode);
    setName(originalName);
    setDescription(originalDescription);
    setShowDiff(false);
  };

  const handleCodeUpdate = useCallback((newCode: string) => {
    setCode(newCode);
    if (validateTimer.current) clearTimeout(validateTimer.current);
    validateTimer.current = setTimeout(() => runValidation(newCode), 300);
  }, [runValidation]);

  if (!loaded) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    );
  }

  if (notFound) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <Code2 className="w-12 h-12 mb-3 opacity-30" />
        <p className="text-sm font-medium">{t("tools.toolNotFound")}</p>
        <button onClick={() => navigate("/tools")} className="mt-3 text-xs text-blue-500 hover:underline">
          {t("common.back")}
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className={`flex items-center gap-3 px-6 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
        <button onClick={() => navigate("/tools")} className={`p-1 rounded ${isDark ? "hover:bg-gray-800 text-gray-300" : "hover:bg-gray-100 text-gray-600"}`} title={t("common.back")}>
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>{name || toolId}</h2>
          {description && <p className="text-xs text-gray-400 truncate">{description}</p>}
        </div>

        {/* Validate */}
        <button
          onClick={handleValidate}
          disabled={validating}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors disabled:opacity-50 ${isDark ? "text-gray-400 hover:text-green-400 hover:bg-green-900/30" : "text-gray-500 hover:text-green-600 hover:bg-green-50"}`}
        >
          {validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
          {t("common.validate")}
        </button>

        {/* Diff */}
        {hasChanges && (
          <button
            onClick={() => setShowDiff(true)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-blue-400 hover:bg-blue-900/30" : "text-gray-500 hover:text-blue-600 hover:bg-blue-50"}`}
          >
            <GitCompare className="w-3.5 h-3.5" />
            {t("tools.showChanges")}
          </button>
        )}

        {/* Discard */}
        {hasChanges && (
          <button
            onClick={handleDiscard}
            className={`px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}
          >
            {t("common.discard")}
          </button>
        )}

        {/* Save */}
        {hasChanges && (
          <>
            <div className={`w-px h-5 ${isDark ? "bg-gray-700" : "bg-gray-200"} mx-0.5`} />
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all"
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t("common.save")}
            </button>
          </>
        )}

        {/* Delete */}
        <button
          onClick={() => setConfirmDelete(true)}
          className={`p-1.5 rounded transition-colors ${isDark ? "text-red-400 hover:bg-red-900/20" : "text-red-400 hover:text-red-600 hover:bg-red-50"}`}
          title={t("common.delete")}
        >
          <Trash2 className="w-4 h-4" />
        </button>

        {/* AI Assistant toggle */}
        <button
          onClick={() => {
            if (toolId) {
              if (panelOpen) useToolAssistantStore.getState().closePanel();
              else openPanel(toolId);
            }
          }}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${
            panelOpen
              ? isDark ? "bg-purple-900/30 text-purple-400" : "bg-purple-50 text-purple-600"
              : isDark ? "text-gray-400 hover:text-purple-400 hover:bg-purple-900/30" : "text-gray-500 hover:text-purple-600 hover:bg-purple-50"
          }`}
          title={t("assistant.title")}
        >
          <Sparkles className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Validation results */}
      {validationResult && (
        <div className={`mx-6 mt-2 rounded-lg text-sm border ${
          !validationResult.valid
            ? isDark ? "bg-red-900/20 border-red-800" : "bg-red-50 border-red-200"
            : validationResult.errors.length === 0 && validationResult.warnings.length === 0
              ? isDark ? "bg-green-900/20 border-green-800" : "bg-green-50 border-green-200"
              : isDark ? "bg-amber-900/20 border-amber-800" : "bg-amber-50 border-amber-200"
        }`}>
          <div className="px-4 py-2">
            <div className="flex items-center justify-between">
              <p className={`text-xs font-medium ${
                !validationResult.valid ? "text-red-500"
                  : validationResult.errors.length === 0 && validationResult.warnings.length === 0
                    ? isDark ? "text-green-400" : "text-green-600"
                    : "text-amber-600"
              }`}>
                {!validationResult.valid
                  ? t("validation.failed")
                  : validationResult.errors.length === 0 && validationResult.warnings.length === 0
                    ? t("validation.noIssues")
                    : t("validation.passedWithWarnings", { count: validationResult.warnings.length })}
              </p>
              <button onClick={() => setValidationResult(null)} className="text-gray-400 hover:text-gray-600">
                <X className="w-3 h-3" />
              </button>
            </div>
            {validationResult.errors.map((e, i) => (
              <p key={`e${i}`} className="text-[11px] text-red-500 mt-1">&#x2716; {e}</p>
            ))}
            {validationResult.warnings.map((w, i) => (
              <p key={`w${i}`} className="text-[11px] text-amber-600 mt-1">&#x26A0; {w}</p>
            ))}
            {canEdit && (validationResult.errors.length > 0 || validationResult.warnings.length > 0) && (
              <button
                onClick={() => {
                  const issues = [
                    ...validationResult.errors.map(e => `Error: ${e}`),
                    ...validationResult.warnings.map(w => `Warning: ${w}`),
                  ].join("\n");
                  if (toolId) {
                    openPanel(toolId);
                    setTimeout(() => {
                      const store = useToolAssistantStore.getState();
                      store.sendMessage(
                        `## Auto-Fix Task\nFix ONLY the following validation issues. Do NOT remove or rewrite any existing content.\n\nIssues:\n${issues}\n\nRules:\n- Use __tool_code (4 backticks) to output the COMPLETE fixed tool function.\n- Fix ONLY the specific issues listed above.\n- NEVER delete existing content, sections, or descriptions.\n- NEVER shorten or summarize existing text.\n- If an issue appears already fixed in the current code, skip it and say so.\n- Do NOT ask for confirmation. Execute fixes immediately.`,
                        { name, description, category: "custom", code },
                        handleCodeUpdate,
                      );
                    }, 100);
                  }
                  setValidationResult(null);
                }}
                className={`mt-2 flex items-center gap-1 px-2 py-1 text-[11px] rounded transition-colors ${isDark ? "text-blue-400 hover:bg-blue-900/30" : "text-blue-600 hover:bg-blue-50"}`}
              >
                <Sparkles className="w-3 h-3" />
                {t("common.autoFix")}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex flex-1 min-h-0">
        <div className="flex flex-col flex-1 min-w-0">
          {/* Metadata */}
          <div className={`flex items-center gap-3 px-4 py-2 border-b ${isDark ? "border-gray-800 bg-gray-900/50" : "border-gray-100 bg-gray-50/50"}`}>
            <div className="flex items-center gap-1.5">
              <label className="text-[10px] text-gray-500">{t("tools.name")}</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className={`px-2 py-1 text-xs border rounded w-36 outline-none focus:ring-1 focus:ring-blue-500 ${isDark ? "border-gray-700 bg-gray-800 text-gray-200" : "border-gray-200 bg-white text-gray-800"}`}
                placeholder={t("tools.namePlaceholder")}
              />
            </div>
            <div className="flex items-center gap-1.5 flex-1">
              <label className="text-[10px] text-gray-500">{t("tools.description")}</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className={`flex-1 px-2 py-1 text-xs border rounded outline-none focus:ring-1 focus:ring-blue-500 ${isDark ? "border-gray-700 bg-gray-800 text-gray-200" : "border-gray-200 bg-white text-gray-800"}`}
                placeholder={t("tools.descPlaceholder")}
              />
            </div>
          </div>

          {/* Editor */}
          <div className="flex-1 min-h-0">
            <Editor
              language="python"
              theme={isDark ? "vs-dark" : "light"}
              value={code}
              onChange={handleCodeChange}
              onMount={handleEditorMount}
              options={{ minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, tabSize: 4, insertSpaces: true, wordWrap: "on", automaticLayout: true }}
            />
          </div>

          {/* Error bar */}
          {(error || validationError) && (
            <div className={`px-4 py-2 text-xs flex items-center justify-between ${isDark ? "bg-red-900/20 border-t border-red-800 text-red-400" : "bg-red-50 border-t border-red-200 text-red-600"}`}>
              <span>{validationError || error}</span>
              <button onClick={() => { setValidationError(null); clearError(); }} className="text-red-400 hover:text-red-600 text-[10px]">{t("common.dismiss")}</button>
            </div>
          )}
        </div>

        {/* AI Assistant */}
        {panelOpen && toolId && (
          <ToolAssistant
            toolId={toolId}
            currentCode={code}
            toolName={name}
            toolDescription={description}
            toolCategory="custom"
            onCodeUpdate={handleCodeUpdate}
          />
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
                <span className={`text-xs px-2 py-0.5 rounded-full font-mono ${isDark ? "bg-blue-900/40 text-blue-300" : "bg-blue-100 text-blue-700"}`}>
                  {extractFuncName(code) || toolId}
                </span>
              </div>
              <button onClick={() => setShowDiff(false)} className={`text-[12px] px-2 py-1 rounded ${isDark ? "text-gray-400 hover:text-gray-200 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}>
                {t("common.close")}
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <DiffEditor
                original={originalCode}
                modified={code}
                language="python"
                theme={isDark ? "vs-dark" : "light"}
                keepCurrentOriginalModel={true}
                keepCurrentModifiedModel={true}
                options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, renderSideBySide: true }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDelete(false)} onKeyDown={(e) => { if (e.key === "Escape") setConfirmDelete(false); }}>
          <div className={`rounded-xl shadow-2xl p-5 max-w-sm mx-4 ${isDark ? "bg-gray-800" : "bg-white"}`} onClick={(e) => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.moveToTrash")}</p>
            <p className="text-xs text-gray-500 mb-4">{t("tools.deleteConfirm", { name: name || toolId })}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDelete(false)} className={`px-3 py-1.5 text-xs rounded-lg ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"}`}>
                {t("common.cancel")}
              </button>
              <button onClick={handleDelete} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">
                {t("skillEditor.moveToTrashBtn")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Unsaved changes blocker */}
      {blocker.state === "blocked" && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onKeyDown={(e) => { if (e.key === "Escape") blocker.reset?.(); }}>
          <div className={`rounded-xl shadow-2xl p-5 max-w-sm mx-4 ${isDark ? "bg-gray-800" : "bg-white"}`}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.unsavedChanges")}</p>
            <p className="text-xs text-gray-500 mb-4">{t("skillEditor.unsavedDesc", { count: 1 })}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => blocker.reset?.()} className={`px-3 py-1.5 text-xs rounded-lg ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"}`}>
                {t("skillEditor.stay")}
              </button>
              <button onClick={() => blocker.proceed?.()} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">
                {t("skillEditor.discardLeave")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
