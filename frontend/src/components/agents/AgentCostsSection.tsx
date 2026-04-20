import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import { fetchAgentCosts, type AgentCostsResponse, type CostRange } from "../../lib/api-client";

const RANGES: CostRange[] = ["7d", "30d"];

function formatUsd(v: number): string {
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v > 0) return `$${v.toFixed(4)}`;
  return "$0.00";
}

function formatNumber(n: number): string {
  return new Intl.NumberFormat().format(Math.round(n || 0));
}

export default function AgentCostsSection({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [range, setRange] = useState<CostRange>("7d");
  const [data, setData] = useState<AgentCostsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAgentCosts(agentId, range)
      .then((r) => {
        if (!cancelled) {
          setData(r);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err as Error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, range, tick]);

  return (
    <div className="p-4" data-testid="agent-costs-section">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            {t("costs.agentSectionTitle")}
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("costs.agentSectionSubtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="inline-flex overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600 text-xs"
            role="tablist"
          >
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={range === r}
                onClick={() => setRange(r)}
                data-testid={`agent-costs-range-${r}`}
                className={`px-2.5 py-1 ${
                  range === r
                    ? "bg-blue-500 text-white"
                    : "bg-white text-gray-700 hover:bg-gray-100 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
                }`}
              >
                {t(`costs.range.${r}`)}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => setTick((x) => x + 1)}
            disabled={loading}
            className="rounded p-1 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
            aria-label={t("common.refresh")}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div
        className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-800 dark:border-amber-600 dark:bg-amber-900/20 dark:text-amber-200"
      >
        {t("costs.disclaimer")}
      </div>

      {error && (
        <div className="text-sm text-red-600 dark:text-red-400">
          {t("costs.loadError")}: {error.message}
        </div>
      )}

      {loading && !data && (
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {data && (
        <table className="w-full text-sm">
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            <tr>
              <td className="py-2 text-xs text-gray-500 dark:text-gray-400">{t("costs.col.calls")}</td>
              <td className="py-2 text-right font-mono" data-testid="agent-costs-calls">
                {formatNumber(data.calls)}
              </td>
            </tr>
            <tr>
              <td className="py-2 text-xs text-gray-500 dark:text-gray-400">{t("costs.col.inputTokens")}</td>
              <td className="py-2 text-right font-mono">{formatNumber(data.inputTokens)}</td>
            </tr>
            <tr>
              <td className="py-2 text-xs text-gray-500 dark:text-gray-400">{t("costs.col.outputTokens")}</td>
              <td className="py-2 text-right font-mono">{formatNumber(data.outputTokens)}</td>
            </tr>
            <tr>
              <td className="py-2 text-xs text-gray-500 dark:text-gray-400">{t("costs.col.model")}</td>
              <td className="py-2 text-right text-xs text-gray-600 dark:text-gray-400">
                {data.modelId || "—"}
              </td>
            </tr>
            <tr>
              <td className="py-2 text-xs text-gray-500 dark:text-gray-400">{t("costs.col.cost")}</td>
              <td className="py-2 text-right font-mono text-base font-semibold">
                {formatUsd(data.costUsd)}
              </td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}
