import { useState, useEffect } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { GitCompare } from "lucide-react";
import { useTranslation } from "react-i18next";
import useIsDark from "../../hooks/useIsDark";
import { getMonacoLanguage } from "../../lib/monaco-helpers";

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

  const FIELD_LABELS: Record<string, string> = {
    name: t("agentEditor.name"), display_name: t("agentEditor.displayName"), description: t("agentEditor.description"),
    system_prompt: t("agentEditor.systemPrompt"), tool_definitions: t("agentEditor.tools"), tool_names: t("agentEditor.toolNames"),
    welcome_message: t("agentEditor.welcomeMessage"), suggestions: t("agentEditor.suggestions"), template_id: t("agentEditor.template"),
    default_model_id: t("agentEditor.defaultModel"), supports_images: t("agentEditor.imageSupport"), model_id: t("agentEditor.defaultModel"),
  };

  // ESC to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  if (entries.length === 0) return null;

  const [key, { old: oldVal, new: newVal }] = entries[activeIdx];
  const label = FIELD_LABELS[key] || key;
  const lang = key === "tool_definitions" ? "python"
    : key === "system_prompt" ? "markdown"
    : key.includes("/") ? getMonacoLanguage(key.split("/").pop() || key)
    : "plaintext";

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onCancel}>
      <div className="bg-white dark:bg-gray-900 rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">{viewOnly ? t("reviewChanges.viewTitle") : t("reviewChanges.title")}</span>
            <div className="flex items-center gap-1">
              {entries.map(([k], i) => (
                <button key={k} onClick={() => setActiveIdx(i)}
                  className={`px-2 py-0.5 text-[11px] rounded ${i === activeIdx
                    ? "bg-blue-600 text-white"
                    : "text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                  }`}>
                  {FIELD_LABELS[k] || k}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onCancel} className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg">{viewOnly ? t("common.close") : t("common.cancel")}</button>
            {!viewOnly && onConfirm && (
              <button onClick={onConfirm} className="px-4 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700">{t("reviewChanges.confirmDeploy")}</button>
            )}
          </div>
        </div>
        <div className="px-4 py-1.5 text-xs font-semibold border-b text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
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

export default ReviewChangesModal;
