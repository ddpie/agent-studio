import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useNavigate, useSearchParams, useBlocker } from "react-router";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft, Loader2, Save, Trash2, GitCompare, Sparkles, Code2,
} from "lucide-react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { useToolLibraryStore, type ToolTemplate } from "../../stores/tool-library-store";
import { useToolAssistantStore } from "../../stores/tool-assistant-store";
import { useUISettings } from "../../stores/ui-settings-store";
import { preloadPyodide, isPyodideReady, checkPythonSyntax } from "../../lib/pyodide-checker";
import ToolAssistant from "../tools/ToolAssistant";

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

function useIsDark() {
  const { theme } = useUISettings();
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function validatePython(code: string): { line: number; col: number; message: string; severity: number }[] {
  const markers: { line: number; col: number; message: string; severity: number }[] = [];
  const lines = code.split("\n");

  let parens = 0, brackets = 0, braces = 0;
  for (const line of lines) {
    for (const ch of line) {
      if (ch === "(") parens++; else if (ch === ")") parens--;
      else if (ch === "[") brackets++; else if (ch === "]") brackets--;
      else if (ch === "{") braces++; else if (ch === "}") braces--;
    }
  }
  if (parens !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced parentheses", severity: 8 });
  if (brackets !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced brackets", severity: 8 });
  if (braces !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced braces", severity: 8 });

  if (!code.includes("@tool")) {
    markers.push({ line: 1, col: 1, message: "Missing @tool decorator", severity: 8 });
  }

  let tripleCount = 0;
  for (const line of lines) {
    const matches = line.match(/"""/g);
    if (matches) tripleCount += matches.length;
  }
  if (tripleCount % 2 !== 0) {
    markers.push({ line: lines.length, col: 1, message: "Unterminated triple-quoted string", severity: 8 });
  }

  return markers;
}

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

  const { tools, fetchTools, saveTool, deleteTool, saving, error, clearError } = useToolLibraryStore();
  const { panelOpen, openPanel } = useToolAssistantStore();

  const isNew = searchParams.get("new") === "1";
  const paramName = searchParams.get("name") || "";
  const paramDesc = searchParams.get("desc") || "";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [code, setCode] = useState("");
  const [originalCode, setOriginalCode] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [fetchAttempted, setFetchAttempted] = useState(false);

  const editorRef = useRef<MonacoNS.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof MonacoNS | null>(null);
  const validateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef<(() => void) | undefined>(undefined);

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
      setCode(TOOL_TEMPLATE);
      setOriginalCode("");
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
    setCode(tool.code);
    setOriginalCode(tool.code);
    setLoaded(true);
    if (toolId) openPanel(toolId);
  }, [tools, toolId, isNew, paramName, paramDesc, fetchTools]);

  const hasChanges = code !== originalCode;

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

  // Validation
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

  // Save
  const handleSave = async () => {
    clearError();
    const funcName = extractFuncName(code);
    if (!funcName) {
      setValidationError(t("tools.missingDecorator"));
      return;
    }
    // Check name conflict with other tools
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
      await deleteTool(toolId);
      navigate("/tools");
    } catch {
      // error is set in store
    }
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

        {hasChanges && (
          <button
            onClick={() => setShowDiff(!showDiff)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${
              showDiff
                ? isDark ? "bg-blue-900/30 text-blue-400" : "bg-blue-50 text-blue-600"
                : isDark ? "text-gray-400 hover:text-blue-400 hover:bg-blue-900/30" : "text-gray-500 hover:text-blue-600 hover:bg-blue-50"
            }`}
          >
            <GitCompare className="w-3.5 h-3.5" />
            {showDiff ? t("tools.hideChanges") : t("tools.showChanges")}
          </button>
        )}

        {hasChanges && (
          <button
            onClick={() => { setCode(originalCode); setShowDiff(false); }}
            className={`px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}
          >
            {t("common.discard")}
          </button>
        )}

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

        <button
          onClick={() => setConfirmDelete(true)}
          className={`p-1.5 rounded transition-colors ${isDark ? "text-red-400 hover:bg-red-900/20" : "text-red-400 hover:text-red-600 hover:bg-red-50"}`}
          title={t("common.delete")}
        >
          <Trash2 className="w-4 h-4" />
        </button>

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
            {showDiff && originalCode ? (
              <DiffEditor
                original={originalCode}
                modified={code}
                language="python"
                theme={isDark ? "vs-dark" : "light"}
                options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, renderSideBySide: true }}
              />
            ) : (
              <Editor
                language="python"
                theme={isDark ? "vs-dark" : "light"}
                value={code}
                onChange={handleCodeChange}
                onMount={handleEditorMount}
                options={{ minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, tabSize: 4, insertSpaces: true, wordWrap: "on", automaticLayout: true }}
              />
            )}
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

      {/* Delete confirm */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDelete(false)} onKeyDown={(e) => { if (e.key === "Escape") setConfirmDelete(false); }}>
          <div className={`rounded-xl shadow-2xl p-5 max-w-sm mx-4 ${isDark ? "bg-gray-800" : "bg-white"}`} onClick={(e) => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("common.delete")}?</p>
            <p className="text-xs text-gray-500 mb-4">{t("tools.deleteConfirm")}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDelete(false)} className={`px-3 py-1.5 text-xs rounded-lg ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"}`}>
                {t("common.cancel")}
              </button>
              <button onClick={handleDelete} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">
                {t("common.delete")}
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
