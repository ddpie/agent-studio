import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, X } from "lucide-react";
import type { McpCatalogTarget } from "../../lib/api-client";

export interface UpgradeMcpModalProps {
  target: McpCatalogTarget;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
  /** When true, the new-registry sensitivity exceeds the caller's role. */
  blockedBySensitivity?: boolean;
}

export default function UpgradeMcpModal({
  target,
  onConfirm,
  onCancel,
  blockedBySensitivity,
}: UpgradeMcpModalProps) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fromV = target.runtime?.image_version || "?";
  const toV = target.latestVersion;

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
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {t("mcp.upgrade.confirmTitle")}: {target.name}
          </h2>
          <button
            type="button"
            onClick={onCancel}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-700 dark:text-gray-300">
            {t("mcp.upgrade.versionFromTo", { from: fromV, to: toV })}
          </p>

          {blockedBySensitivity && (
            <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
                <p className="text-xs text-red-800 dark:text-red-300">
                  {t("mcp.upgrade.sensitivityRose")}
                </p>
              </div>
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
            disabled={submitting || blockedBySensitivity}
            className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
            {t("mcp.action.upgrade")}
          </button>
        </div>
      </div>
    </div>
  );
}
