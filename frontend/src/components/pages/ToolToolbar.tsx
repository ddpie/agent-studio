import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, Trash2, Loader2, Save, GitCompare, ShieldCheck, Sparkles } from "lucide-react";
import useIsDark from "../../hooks/useIsDark";

interface ToolToolbarProps {
  toolName: string;
  description: string;
  hasChanges: boolean;
  saving: boolean;
  validating: boolean;
  assistantOpen: boolean;
  onBack: () => void;
  onSave: () => void;
  onDiscard: () => void;
  onValidate: () => void;
  onShowDiff: () => void;
  onDelete: () => void;
  onToggleAssistant: () => void;
  extraSlot?: ReactNode;
}

export default function ToolToolbar({
  toolName, description, hasChanges, saving, validating, assistantOpen,
  onBack, onSave, onDiscard, onValidate, onShowDiff, onDelete, onToggleAssistant,
  extraSlot,
}: ToolToolbarProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();

  return (
    <div className={`flex items-center gap-3 px-6 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
      <button onClick={onBack} className={`p-1 rounded ${isDark ? "hover:bg-gray-800 text-gray-300" : "hover:bg-gray-100 text-gray-600"}`} title={t("common.back")}>
        <ChevronLeft className="w-4 h-4" />
      </button>
      <div className="flex-1 min-w-0">
        <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>{toolName}</h2>
        {description && <p className="text-xs text-gray-400 truncate">{description}</p>}
      </div>
      {extraSlot}
      <button onClick={onValidate} disabled={validating}
        className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors disabled:opacity-50 ${isDark ? "text-gray-400 hover:text-green-400 hover:bg-green-900/30" : "text-gray-500 hover:text-green-600 hover:bg-green-50"}`}>
        {validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
        {t("common.validate")}
      </button>
      {hasChanges && (
        <button onClick={onShowDiff}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-blue-400 hover:bg-blue-900/30" : "text-gray-500 hover:text-blue-600 hover:bg-blue-50"}`}>
          <GitCompare className="w-3.5 h-3.5" />
          {t("tools.showChanges")}
        </button>
      )}
      {hasChanges && (
        <button onClick={onDiscard}
          className={`px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}>
          {t("common.discard")}
        </button>
      )}
      {hasChanges && (
        <>
          <div className={`w-px h-5 ${isDark ? "bg-gray-700" : "bg-gray-200"} mx-0.5`} />
          <button onClick={onSave} disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {t("common.save")}
          </button>
        </>
      )}
      <button onClick={onDelete} className={`p-1.5 rounded transition-colors ${isDark ? "text-red-400 hover:bg-red-900/20" : "text-red-400 hover:text-red-600 hover:bg-red-50"}`} title={t("common.delete")}>
        <Trash2 className="w-4 h-4" />
      </button>
      <button onClick={onToggleAssistant}
        className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${assistantOpen
          ? isDark ? "bg-purple-900/30 text-purple-400" : "bg-purple-50 text-purple-600"
          : isDark ? "text-gray-400 hover:text-purple-400 hover:bg-purple-900/30" : "text-gray-500 hover:text-purple-600 hover:bg-purple-50"
        }`}
        title={t("assistant.title")}>
        <Sparkles className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
