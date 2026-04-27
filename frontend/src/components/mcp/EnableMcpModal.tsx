import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, X } from "lucide-react";
import type { McpCatalogTarget } from "../../lib/api-client";

export interface EnableMcpModalProps {
  target: McpCatalogTarget;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
  /** True if the user role doesn't satisfy the sensitivity gate. */
  disabled?: boolean;
}

export default function EnableMcpModal({
  target,
  onConfirm,
  onCancel,
  disabled,
}: EnableMcpModalProps) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // requires_env fallback: v4 defers env flow; any target with requires_env
  // will 400 from the backend. UI surfaces this up-front.
  const requiresEnv = false; // catalog doesn't expose requires_env yet; backend gate catches it

  const body =
    target.sensitivity === "high"
      ? t("mcp.enableConfirm.bodyHigh")
      : target.sensitivity === "medium"
        ? t("mcp.enableConfirm.bodyMed")
        : t("mcp.enableConfirm.bodyLow");

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg w-full max-w-md shadow-xl">
        <div className="flex items-start justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {t("mcp.enableConfirm.title")}: {target.name}
            </h2>
            {target.description && (
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                {target.description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-700 dark:text-gray-300">{body}</p>

          {target.sensitivity !== "low" && target.sensitiveReasons.length > 0 && (
            <div className="rounded-md border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 p-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                <div className="text-xs text-amber-900 dark:text-amber-200">
                  <ul className="list-disc pl-4 space-y-0.5">
                    {target.sensitiveReasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}

          {disabled && (
            <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3">
              <p className="text-xs text-red-800 dark:text-red-300">
                {t("mcp.enableConfirm.requiresAdmin")}
              </p>
            </div>
          )}

          {requiresEnv && (
            <div className="rounded-md border border-orange-200 dark:border-orange-900 bg-orange-50 dark:bg-orange-950/30 p-3">
              <p className="text-xs text-orange-800 dark:text-orange-300">
                {t("mcp.error.envMissing")}
              </p>
            </div>
          )}

          {error && (
            <p className="text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="text-xs px-3 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={disabled || submitting || requiresEnv}
            className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
            {t("mcp.action.enable")}
          </button>
        </div>
      </div>
    </div>
  );
}
