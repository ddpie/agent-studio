import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useAgentEvaluations, type EvaluatorStats } from "../../hooks/useAgentEvaluations";
import {
  enableAgentEvaluations,
  getAgentEvaluationStatus,
  type AgentEvaluationStatus,
} from "../../lib/api-client";

export interface EvaluationsTabProps {
  agentId: string;
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color =
    value >= 0.8 ? "bg-emerald-500" :
    value >= 0.5 ? "bg-amber-500" :
    "bg-red-500";
  return (
    <div className="w-24 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

function EvaluatorRow({ evaluator, stats }: { evaluator: string; stats: EvaluatorStats }) {
  return (
    <tr className="border-t dark:border-gray-800" data-testid={`evaluator-row-${evaluator}`}>
      <td className="py-2 font-mono text-xs">{evaluator.replace(/^Builtin\./, "")}</td>
      <td className="py-2" data-score={stats.latest}>
        <div className="flex items-center gap-2">
          <span className="font-mono tabular-nums">{stats.latest.toFixed(2)}</span>
          <ScoreBar value={stats.latest} />
        </div>
      </td>
      <td className="py-2 font-mono tabular-nums text-gray-600 dark:text-gray-400">
        {stats.mean.toFixed(2)}
      </td>
      <td className="py-2 text-gray-500 dark:text-gray-400">{stats.sessions.size}</td>
    </tr>
  );
}

export default function EvaluationsTab({ agentId }: EvaluationsTabProps) {
  const { t } = useTranslation();
  const { grouped, diagnostics, error, loading, refresh } = useAgentEvaluations(agentId);

  const [status, setStatus] = useState<AgentEvaluationStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [enabling, setEnabling] = useState(false);
  const [enableError, setEnableError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    if (!agentId) return;
    setStatusLoading(true);
    try {
      const s = await getAgentEvaluationStatus(agentId);
      setStatus(s);
    } catch {
      setStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const handleEnable = async () => {
    if (!agentId || enabling) return;
    setEnableError(null);
    setEnabling(true);
    try {
      await enableAgentEvaluations(agentId);
      await loadStatus();
    } catch (err) {
      setEnableError(err instanceof Error ? err.message : String(err));
    } finally {
      setEnabling(false);
    }
  };

  const handleRefresh = () => {
    refresh();
    loadStatus();
  };

  const hasRows = grouped && Object.keys(grouped).length > 0;
  const isEmpty = grouped && Object.keys(grouped).length === 0 && !loading;
  const statusIsActive = status?.exists && (status.status ?? "").toUpperCase() === "ACTIVE";

  return (
    <div className="p-4" data-testid="evaluations-tab">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">{t("evaluations.tab")}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("evaluations.subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={loading || statusLoading}
          className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
        >
          <RefreshCw className={`w-3 h-3 ${loading || statusLoading ? "animate-spin" : ""}`} />
          {t("common.refresh")}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400">
          {t("evaluations.loadError")}: {error.message}
        </div>
      )}

      {/* Empty-state branches — only shown when there are no rows to render */}
      {isEmpty && status && !status.exists && (
        <div
          className="rounded-md border border-gray-200 dark:border-gray-700 p-4 text-sm"
          data-testid="evaluations-not-enabled"
        >
          <div className="font-medium text-gray-900 dark:text-gray-100">
            {t("evaluations.notEnabledTitle")}
          </div>
          <div className="text-gray-500 dark:text-gray-400 mt-1">
            {t("evaluations.notEnabledBody")}
          </div>
          <button
            type="button"
            onClick={handleEnable}
            disabled={enabling}
            className="mt-3 inline-flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {enabling ? t("evaluations.enabling") : t("evaluations.enableButton")}
          </button>
          {enableError && (
            <div className="mt-2 text-xs text-red-600 dark:text-red-400">{enableError}</div>
          )}
        </div>
      )}

      {isEmpty && status && status.exists && !statusIsActive && (
        <div
          className="rounded-md border border-gray-200 dark:border-gray-700 p-4 text-sm"
          data-testid="evaluations-config-pending"
        >
          <div className="flex items-center gap-2">
            <span className="font-medium text-gray-900 dark:text-gray-100">
              {t("evaluations.configStatus", { status: status.status ?? "UNKNOWN" })}
            </span>
            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-300 font-mono">
              {status.status ?? "UNKNOWN"}
            </span>
          </div>
          <div className="text-gray-500 dark:text-gray-400 mt-1">
            {(status.status ?? "").toUpperCase() === "FAILED"
              ? t("evaluations.configFailed")
              : t("evaluations.configProvisioning")}
          </div>
        </div>
      )}

      {isEmpty && (statusIsActive || !status) && (
        <div className="text-sm" data-testid="evaluations-empty-active">
          <div className="font-medium text-gray-700 dark:text-gray-300">
            {t("evaluations.noDataTitle")}
          </div>
          <div className="text-gray-500 dark:text-gray-400 mt-1">
            {diagnostics?.allFailed
              ? t("evaluations.spanMappingError")
              : t("evaluations.noDataBody")}
          </div>
        </div>
      )}

      {hasRows && (
        <table className="w-full text-sm" data-testid="evaluations-table">
          <thead className="text-xs text-gray-500 dark:text-gray-400 uppercase">
            <tr>
              <th className="text-left py-2">{t("evaluations.evaluator")}</th>
              <th className="text-left py-2">{t("evaluations.latestScore")}</th>
              <th className="text-left py-2">{t("evaluations.mean")}</th>
              <th className="text-left py-2">{t("evaluations.sessions")}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(grouped).map(([ev, stats]) => (
              <EvaluatorRow key={ev} evaluator={ev} stats={stats} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
