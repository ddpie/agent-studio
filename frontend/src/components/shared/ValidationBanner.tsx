/**
 * ValidationBanner — shared validation result display with auto-fix button.
 */
import { useTranslation } from "react-i18next";
import { Loader2, Sparkles } from "lucide-react";
import useIsDark from "../../hooks/useIsDark";
import type { ValidationResult } from "../../lib/types/validation";

interface ValidationBannerProps {
  result: ValidationResult | null;
  onDismiss: () => void;
  onAutoFix?: () => void;
  autoFixing?: boolean;
}

export default function ValidationBanner({ result, onDismiss, onAutoFix, autoFixing }: ValidationBannerProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();

  if (!result) return null;

  const isSuccess = result.valid && result.errors.length === 0 && result.warnings.length === 0;
  const hasIssues = result.errors.length > 0 || result.warnings.length > 0;

  return (
    <div className={`mx-6 mt-2 rounded-lg text-sm border ${
      !result.valid
        ? isDark ? "bg-red-900/20 border-red-800" : "bg-red-50 border-red-200"
        : isSuccess
          ? isDark ? "bg-green-900/20 border-green-800" : "bg-green-50 border-green-200"
          : isDark ? "bg-amber-900/20 border-amber-800" : "bg-amber-50 border-amber-200"
    }`}>
      <div className="px-4 py-2">
        <div className="flex items-center justify-between">
          <p className={`text-xs font-medium ${
            !result.valid ? "text-red-500"
              : isSuccess
                ? isDark ? "text-green-400" : "text-green-600"
                : "text-amber-600"
          }`}>
            {!result.valid
              ? t("validation.failed")
              : isSuccess
                ? t("validation.noIssues")
                : t("validation.warnings")}
          </p>
        </div>
        {result.errors.map((e, i) => (
          <p key={`e${i}`} className="text-[11px] text-red-500 mt-1">&#x2716; {e}</p>
        ))}
        {result.warnings.map((w, i) => (
          <p key={`w${i}`} className="text-[11px] text-amber-600 mt-1">&#x26A0; {w}</p>
        ))}
        {hasIssues && (
          <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
            {onAutoFix && (
              <button onClick={onAutoFix} disabled={autoFixing}
                className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-green-600 text-white rounded hover:bg-green-700 transition-colors disabled:opacity-50">
                {autoFixing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                {t("common.autoFix")}
              </button>
            )}
            <button onClick={onDismiss} className="text-[10px] text-gray-400 hover:text-gray-600">{t("common.dismiss")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
