import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { X, Loader2 } from "lucide-react";
import { useRunDetail } from "../../hooks/useRuns";
import RunDetail from "./RunDetail";

/**
 * Modal shell around RunDetail. Fetches its own detail+output from the
 * runId, so callers only need to pass ids. ESC and backdrop click close.
 */
export default function RunDetailModal({
  agentId,
  runId,
  onClose,
}: {
  agentId: string;
  runId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { detail, output, loading, error } = useRunDetail(agentId, runId);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
      data-testid="run-detail-modal"
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-lg w-[900px] max-w-full h-[85vh] flex flex-col border border-gray-200 dark:border-gray-800 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex-shrink-0">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            {t("runs.detailTitle", "Run detail")}
          </h3>
          <button
            type="button"
            onClick={onClose}
            data-testid="run-detail-modal-close"
            aria-label={t("common.close", "Close")}
            className="p-1 rounded text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 min-h-0">
          {loading && !detail && (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          )}
          {error && !detail && (
            <div className="p-6 text-sm text-red-600 dark:text-red-400">{error.message}</div>
          )}
          {detail && (
            <RunDetail agentId={agentId} detail={detail} output={output} loading={loading} />
          )}
        </div>
      </div>
    </div>
  );
}
