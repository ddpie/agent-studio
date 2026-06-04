import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play } from "lucide-react";
import { LazyMonacoEditor as Editor } from "../ui/LazyMonaco";
import type * as MonacoNS from "monaco-editor";
import { getMonacoLanguage } from "../../lib/monaco-helpers";
import { applyLintMarkers } from "../../lib/monaco-lint";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import useIsDark from "../../hooks/useIsDark";

interface SkillEditorPaneProps {
  content: string | null;
  currentPath: string;
  onChange: (val: string | undefined) => void;
  loadingContent: boolean;
}

export default function SkillEditorPane({ content, currentPath, onChange, loadingContent }: SkillEditorPaneProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const [running, setRunning] = useState(false);
  const [runOutput, setRunOutput] = useState<string | null>(null);

  const runScript = async () => {
    if (running || !content) return;
    setRunning(true);
    setRunOutput("Running...");
    try {
      const lang = currentPath.endsWith(".py") ? "python" : "shell";
      const prompt = `Run this ${lang} code and return ONLY the output. No explanation.\n\nUse run_command tool with language="${lang === "shell" ? "shell" : "python"}".\n\nCode:\n\`\`\`\n${content}\n\`\`\``;
      let result = "";
      for await (const chunk of invokeMetaAgent(prompt, [])) {
        const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
        if (cleaned) result += cleaned;
      }
      setRunOutput(result.trim() || "(no output)");
    } catch (err) {
      setRunOutput(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setRunning(false);
    }
  };

  if (loadingContent) {
    return <div className="flex justify-center py-12"><Loader2 className="w-5 h-5 animate-spin text-gray-400 dark:text-gray-500" /></div>;
  }

  if (content === null) {
    return <p className="text-sm text-gray-400 dark:text-gray-500 p-6">Failed to load content.</p>;
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <div className={`flex items-center justify-between px-3 py-1.5 border-b ${isDark ? "bg-gray-800 border-gray-700" : "bg-gray-100 border-gray-200"}`}>
        <span className={`text-xs font-mono ${isDark ? "text-gray-300" : "text-gray-600"}`}>{currentPath}</span>
        {(currentPath.endsWith(".py") || currentPath.endsWith(".sh")) && (
          <button onClick={runScript} disabled={running}
            className={`ml-2 flex items-center gap-1 px-2 py-0.5 text-[11px] rounded ${isDark ? "text-green-400 hover:bg-green-900/30" : "text-green-600 hover:bg-green-50"} transition-colors disabled:opacity-50`}
            title={t("skillEditor.runScript")}>
            {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {t("common.run")}
          </button>
        )}
      </div>
      <div className="flex-1">
        <Editor
          value={content}
          onChange={onChange}
          language={getMonacoLanguage(currentPath)}
          theme={isDark ? "vs-dark" : "light"}
          onMount={(editor, monaco) => {
            const model = editor.getModel();
            if (model && content) applyLintMarkers(monaco, model, currentPath, content);
          }}
          onValidate={(markers) => {
            const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
            if (!monacoInstance || !content) { void markers; return; }
            const model = monacoInstance.editor.getModels().find(m => m.getValue() === content);
            if (model) applyLintMarkers(monacoInstance, model, currentPath, content);
            void markers;
          }}
          beforeMount={(monaco) => {
            monaco.languages.json?.jsonDefaults?.setDiagnosticsOptions?.({ validate: true, allowComments: false, schemaValidation: "error" });
            monaco.languages.typescript?.javascriptDefaults?.setDiagnosticsOptions?.({ noSemanticValidation: false, noSyntaxValidation: false });
            monaco.languages.typescript?.typescriptDefaults?.setDiagnosticsOptions?.({ noSemanticValidation: false, noSyntaxValidation: false });
          }}
          options={{
            fontSize: 12, minimap: { enabled: true }, scrollBeyondLastLine: false,
            wordWrap: currentPath.endsWith(".md") ? "on" : "off",
            lineNumbers: "on", folding: true, automaticLayout: true, tabSize: 2, contextmenu: false,
          }}
        />
      </div>
      {runOutput !== null && (
        <div className={`border-t flex-shrink-0 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-gray-50"} max-h-48 overflow-auto`}>
          <div className={`flex items-center justify-between px-3 py-1 ${isDark ? "bg-gray-800" : "bg-gray-100"}`}>
            <span className={`text-[10px] font-semibold uppercase tracking-wider ${isDark ? "text-gray-500" : "text-gray-400"}`}>{t("skillEditor.output")}</span>
            <button onClick={() => setRunOutput(null)} className={`text-[10px] ${isDark ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}>{t("common.close")}</button>
          </div>
          <pre className={`px-3 py-2 text-[11px] font-mono whitespace-pre-wrap ${isDark ? "text-gray-300" : "text-gray-700"}`}>{runOutput}</pre>
        </div>
      )}
    </div>
  );
}
