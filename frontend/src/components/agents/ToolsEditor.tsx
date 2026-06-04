import { useState, useRef, useEffect } from "react";
import MonacoEditor from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { Plus, Trash2, Sparkles, Maximize2, Minimize2 } from "lucide-react";
import useIsDark from "../../hooks/useIsDark";
import { useTranslation } from "react-i18next";
import { preloadPyodide, checkPythonSyntax, isPyodideReady } from "../../lib/pyodide-checker";
import ToolPicker from "./ToolPicker";

/** Extract function name from a @tool code block */
function extractFuncName(code: string): string {
  const m = /def\s+(\w+)\s*\(/.exec(code);
  return m ? m[1] : "unnamed";
}

/** Extract first line of docstring as description */
function extractDocstring(code: string): string {
  const m = /"""(.+?)"""|'''(.+?)'''/s.exec(code);
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

  // Sync blocks when value changes externally (e.g., from AI assistant)
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
  const [pickerOpen, setPickerOpen] = useState(false);

  const existingToolNames = blocks.map(extractFuncName).filter(n => n !== "unnamed");

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

  // Ensure pyodide is preloaded
  useEffect(() => { preloadPyodide(); }, []);

  // Fullscreen overlay
  if (fullscreenIdx !== null && blocks[fullscreenIdx] !== undefined) {
    const code = blocks[fullscreenIdx];
    const name = extractFuncName(code);
    const desc = extractDocstring(code);
    return (
      <div className={`fixed inset-0 z-50 flex flex-col ${isDark ? "bg-gray-900" : "bg-white"}`}>
        <div className={`flex items-center justify-between px-4 py-2 border-b ${isDark ? "bg-gray-800 border-gray-700" : "bg-gray-100 border-gray-200"}`}>
          <span className={`text-sm font-mono ${isDark ? "text-gray-200" : "text-gray-800"}`}>
            <span className="text-blue-500">@tool</span> {name}
            {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
          </span>
          <button
            onClick={() => setFullscreenIdx(null)}
            className={`flex items-center gap-1 px-2 py-1 text-xs rounded transition-colors ${isDark ? "text-gray-400 hover:text-white bg-gray-700 hover:bg-gray-600" : "text-gray-500 hover:text-gray-900 bg-gray-200 hover:bg-gray-300"}`}
          >
            <Minimize2 className="w-3.5 h-3.5" /> {t("agentEditor.exitFullscreen")}
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <MonacoEditor
            value={code}
            onChange={(v) => { if (v !== undefined && v !== code) updateBlock(fullscreenIdx, v); }}
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
            options={{ fontSize: 13, minimap: { enabled: true }, scrollBeyondLastLine: false, automaticLayout: true, contextmenu: false }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {blocks.length === 0 && (
        <p className="text-xs text-gray-400 dark:text-gray-500 italic">{t("agentEditor.noTools")}</p>
      )}
      {blocks.map((code, idx) => {
        const name = extractFuncName(code);
        const desc = extractDocstring(code);
        const isCollapsed = collapsed[idx] ?? false;
        return (
          <div key={idx} className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
            <div
              className={`flex items-center justify-between px-3 py-1.5 border-b cursor-pointer select-none ${isDark ? "bg-gray-800 border-gray-700" : "bg-gray-50 border-gray-200"}`}
              onClick={() => toggleCollapse(idx)}
            >
              <span className={`text-xs font-mono truncate ${isDark ? "text-gray-300" : "text-gray-700"}`}>
                <span className="text-gray-400 mr-1">{isCollapsed ? "▶" : "▼"}</span>
                <span className="text-blue-500">@tool</span> {name !== "unnamed" ? name : <span className="text-gray-400 italic">unnamed</span>}
                {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
              </span>
              <div className="flex items-center gap-1">
                {onOptimizeTool && (
                  <button
                    onClick={(e) => { e.stopPropagation(); onOptimizeTool(name, code); }}
                    className="p-1 text-gray-400 hover:text-purple-500 transition-colors"
                    title={t("agentEditor.optimize", { label: "tool" })}
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                  </button>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); setFullscreenIdx(idx); }}
                  className={`p-1 transition-colors ${isDark ? "text-gray-500 hover:text-white" : "text-gray-400 hover:text-gray-700"}`}
                  title={t("agentEditor.fullscreen")}
                >
                  <Maximize2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); removeBlock(idx); }}
                  className="p-1 text-gray-400 hover:text-red-500 transition-colors"
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
                  options={{ fontSize: 12, minimap: { enabled: false }, scrollBeyondLastLine: false, automaticLayout: true, tabSize: 4, contextmenu: false }}
                />
              </div>
            )}
          </div>
        );
      })}
      <div className="flex gap-2">
        <button
          onClick={() => setPickerOpen(true)}
          className="flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 px-3 py-2 border border-dashed border-blue-300 dark:border-blue-700 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors flex-1 justify-center"
        >
          <Plus className="w-3.5 h-3.5" /> {t("agentEditor.addTool")}
        </button>
        <button
          onClick={addBlock}
          className="flex items-center gap-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors flex-1 justify-center"
        >
          <Plus className="w-3.5 h-3.5" /> {t("agentEditor.createNewTool")}
        </button>
      </div>
      {/* Tool Picker */}
      <ToolPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={(code) => sync([...blocks, code])}
        existingToolNames={existingToolNames}
      />
      {/* Delete confirmation modal */}
      {confirmDeleteIdx !== null && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDeleteIdx(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium text-gray-800 dark:text-gray-200 mb-1">{t("agentEditor.removeTool")}</p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
              {t("agentEditor.deleteToolConfirm")}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDeleteIdx(null)} className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg">{t("common.cancel")}</button>
              <button onClick={confirmRemove} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ToolsEditor;
