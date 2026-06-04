import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, CheckCircle, AlertCircle, Clock } from "lucide-react";

interface IngestionInfo {
  status: string;
  documentsScanned: number;
  documentsIndexed: number;
  documentsFailed: number;
  processedSuccessfully?: number;
  failureReasons?: string[];
}

interface Props {
  ingestion: IngestionInfo | null;
  onRefresh: () => Promise<void>;
}

export default function KBIngestionStatus({ ingestion, onRefresh }: Props) {
  const { t } = useTranslation();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startRef = useRef<number>(Date.now());

  useEffect(() => {
    if (ingestion?.status !== "IN_PROGRESS") {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    startRef.current = Date.now();

    const tick = () => {
      const elapsed = Date.now() - startRef.current;
      if (elapsed > 10 * 60 * 1000) {
        // After 10 min, stop polling
        if (intervalRef.current) {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
        return;
      }
      onRefresh();
    };

    // Start with 10s, after 2min slow to 30s
    const getInterval = () => {
      const elapsed = Date.now() - startRef.current;
      return elapsed > 2 * 60 * 1000 ? 30000 : 10000;
    };

    intervalRef.current = setInterval(() => {
      tick();
      // Adjust interval if needed
      const newInterval = getInterval();
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = setInterval(tick, newInterval);
      }
    }, getInterval());

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [ingestion?.status, onRefresh]);

  if (!ingestion || ingestion.status === "NONE" || ingestion.status === "") {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
        <Clock className="w-4 h-4 text-gray-400 dark:text-gray-500" />
        <span className="text-xs text-gray-500 dark:text-gray-400">
          {t("kb.ingestionNone")}
        </span>
      </div>
    );
  }

  if (ingestion.status === "IN_PROGRESS") {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800">
        <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
        <span className="text-xs text-blue-700 dark:text-blue-400 font-medium">
          {t("kb.ingesting")}
        </span>
      </div>
    );
  }

  if (ingestion.status === "COMPLETE") {
    return (
      <div className="px-3 py-2 rounded-lg bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
        <div className="flex items-center gap-2">
          <CheckCircle className="w-4 h-4 text-green-500" />
          <span className="text-xs text-green-700 dark:text-green-400 font-medium">
            {t("kb.ingestionComplete")}
          </span>
        </div>
        <div className="mt-1 text-[11px] text-green-600 dark:text-green-400 ml-6">
          {ingestion.documentsFailed === 0
            ? t("kb.ingestionStatsSuccess", { count: ingestion.processedSuccessfully ?? ingestion.documentsScanned })
            : t("kb.ingestionStatsPartial", { success: ingestion.processedSuccessfully ?? (ingestion.documentsScanned - ingestion.documentsFailed), failed: ingestion.documentsFailed })}
        </div>
      </div>
    );
  }

  if (ingestion.status === "FAILED") {
    return (
      <div className="px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
        <div className="flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-500" />
          <span className="text-xs text-red-700 dark:text-red-400 font-medium">
            {t("kb.ingestionFailed")}
          </span>
        </div>
        {ingestion.failureReasons && ingestion.failureReasons.length > 0 && (
          <ul className="mt-1 text-[11px] text-red-600 dark:text-red-400 ml-6 list-disc list-inside">
            {ingestion.failureReasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  return null;
}
