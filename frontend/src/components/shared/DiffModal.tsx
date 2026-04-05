/**
 * DiffModal — shared Monaco DiffEditor modal for viewing file changes.
 */
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { DiffEditor } from "@monaco-editor/react";
import { GitCompare } from "lucide-react";
import useIsDark from "../../hooks/useIsDark";
import { getMonacoLanguage } from "../../lib/monaco-helpers";

interface DiffModalProps {
  changes: Map<string, { original: string; edited: string }>;
  onClose: () => void;
}

export default function DiffModal({ changes, onClose }: DiffModalProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const entries = [...changes.entries()];
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (entries.length === 0) return null;

  const [path, { original, edited }] = entries[activeIdx];

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl`} onClick={(e) => e.stopPropagation()}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
          <div className="flex items-center gap-3">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className={`text-sm font-semibold ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.changes")}</span>
            <div className="flex items-center gap-1">
              {entries.map(([p], i) => (
                <button key={p} onClick={() => setActiveIdx(i)}
                  className={`px-2 py-0.5 text-[11px] rounded ${i === activeIdx
                    ? "bg-blue-600 text-white"
                    : isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"
                  }`}>
                  {p}
                </button>
              ))}
            </div>
          </div>
          <button onClick={onClose} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.close")}</button>
        </div>
        <div className="flex-1 overflow-hidden">
          <DiffEditor
            original={original}
            modified={edited}
            language={getMonacoLanguage(path)}
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
