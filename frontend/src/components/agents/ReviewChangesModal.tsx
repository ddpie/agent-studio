import { useState, useEffect } from "react";
import { DiffEditor } from "@monaco-editor/react";
import { GitCompare } from "lucide-react";
import { useTranslation } from "react-i18next";
import useIsDark from "../../hooks/useIsDark";

const FIELD_LABELS: Record<string, string> = {
  name: "Name", display_name: "Display Name", description: "Description",
  system_prompt: "System Prompt", tool_definitions: "Tools", tool_names: "Tool Names",
  welcome_message: "Welcome Message", suggestions: "Suggestions", template_id: "Template",
  default_model_id: "Default Model", supports_images: "Image Support", model_id: "Model",
};

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

export default ReviewChangesModal;
