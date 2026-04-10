import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import Editor from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { isPyodideReady, checkPythonSyntax } from "../../lib/pyodide-checker";
import { validatePython } from "../../lib/validators/python-validator";
import useIsDark from "../../hooks/useIsDark";

interface ToolEditorPaneProps {
  name: string;
  description: string;
  code: string;
  onNameChange: (v: string) => void;
  onDescriptionChange: (v: string) => void;
  onCodeChange: (v: string | undefined) => void;
  editorRef: React.MutableRefObject<MonacoNS.editor.IStandaloneCodeEditor | null>;
  monacoRef: React.MutableRefObject<typeof MonacoNS | null>;
}

export default function ToolEditorPane({
  name, description, code, onNameChange, onDescriptionChange, onCodeChange, editorRef, monacoRef,
}: ToolEditorPaneProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();

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
  }, [editorRef, monacoRef]);

  const handleMount = useCallback((editor: MonacoNS.editor.IStandaloneCodeEditor, monaco: typeof MonacoNS) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    setTimeout(() => runValidation(code), 300);
  }, [runValidation, code, editorRef, monacoRef]);

  return (
    <div className="flex flex-col flex-1 min-w-0">
      <div className={`flex items-center gap-3 px-4 py-2 border-b ${isDark ? "border-gray-800 bg-gray-900/50" : "border-gray-100 bg-gray-50/50"}`}>
        <div className="flex items-center gap-1.5">
          <label className="text-[10px] text-gray-500">{t("tools.name")}</label>
          <input value={name} onChange={(e) => onNameChange(e.target.value)}
            className={`px-2 py-1 text-xs border rounded w-36 outline-none focus:ring-1 focus:ring-blue-500 ${isDark ? "border-gray-700 bg-gray-800 text-gray-200" : "border-gray-200 bg-white text-gray-800"}`}
            placeholder={t("tools.namePlaceholder")} />
        </div>
        <div className="flex items-center gap-1.5 flex-1">
          <label className="text-[10px] text-gray-500">{t("tools.description")}</label>
          <input value={description} onChange={(e) => onDescriptionChange(e.target.value)}
            className={`flex-1 px-2 py-1 text-xs border rounded outline-none focus:ring-1 focus:ring-blue-500 ${isDark ? "border-gray-700 bg-gray-800 text-gray-200" : "border-gray-200 bg-white text-gray-800"}`}
            placeholder={t("tools.descPlaceholder")} />
        </div>
      </div>
      <div className="flex-1 min-h-0">
        <Editor
          language="python"
          theme={isDark ? "vs-dark" : "light"}
          value={code}
          onChange={onCodeChange}
          onMount={handleMount}
          options={{ minimap: { enabled: false }, fontSize: 13, lineNumbers: "on", scrollBeyondLastLine: false, tabSize: 4, insertSpaces: true, wordWrap: "on", automaticLayout: true }}
        />
      </div>
    </div>
  );
}
