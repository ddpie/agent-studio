import { useId, isValidElement, cloneElement } from "react";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

function Field({ label, hint, changed, onOptimize, children }: { label: string; hint?: string; changed?: boolean; onOptimize?: () => void; children: React.ReactNode }) {
  const { t } = useTranslation();
  const fieldId = useId();

  // Inject id into the first valid React element child so the label associates
  const enhanced = isValidElement(children)
    ? cloneElement(children as React.ReactElement<{ id?: string }>, { id: fieldId })
    : children;

  return (
    <div>
      <div className="text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-0.5 flex items-center gap-1">
        <label htmlFor={fieldId}>{label}</label>
        {changed && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" title={t("agentEditor.modified")} />}
        {onOptimize && (
          <button
            type="button"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onOptimize(); }}
            className="ml-auto w-4 h-4 flex items-center justify-center text-gray-300 dark:text-gray-600 hover:text-purple-500 transition-colors rounded"
            title={`AI optimize ${label}`}
          >
            <Sparkles className="w-2.5 h-2.5" />
          </button>
        )}
      </div>
      {enhanced}
      {hint && <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">{hint}</p>}
    </div>
  );
}

export default Field;
