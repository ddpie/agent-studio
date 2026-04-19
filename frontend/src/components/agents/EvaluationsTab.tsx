import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useAgentEvaluations, type EvaluatorStats } from "../../hooks/useAgentEvaluations";

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
      <td className="py-2 text-gray-500">{stats.sessions.size}</td>
    </tr>
  );
}

export default function EvaluationsTab({ agentId }: EvaluationsTabProps) {
  const { t } = useTranslation();
  const { grouped, error, loading, refresh } = useAgentEvaluations(agentId);

  return (
    <div className="p-4" data-testid="evaluations-tab">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold">{t("evaluations.tab")}</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("evaluations.subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("common.refresh")}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400">
          {t("evaluations.loadError")}: {error.message}
        </div>
      )}

      {grouped && Object.keys(grouped).length === 0 && !loading && (
        <div className="text-sm">
          <div className="font-medium">{t("evaluations.emptyTitle")}</div>
          <div className="text-gray-500 dark:text-gray-400 mt-1">{t("evaluations.emptyHint")}</div>
        </div>
      )}

      {grouped && Object.keys(grouped).length > 0 && (
        <table className="w-full text-sm" data-testid="evaluations-table">
          <thead className="text-xs text-gray-500 uppercase">
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
