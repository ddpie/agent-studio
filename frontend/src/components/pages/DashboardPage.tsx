import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getWorkspaceCosts, type WorkspaceCosts } from "../../lib/api-client";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatUsd(n: number): string {
  return `$${n.toFixed(2)}`;
}

const RANGES = ["24h", "7d", "30d"] as const;

export default function DashboardPage() {
  const { t } = useTranslation();
  const [range, setRange] = useState<string>("24h");
  const [data, setData] = useState<WorkspaceCosts | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getWorkspaceCosts(range)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [range]);

  const ws = data?.workspace;
  const agents = data?.agents || [];

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{t("dashboard.title")}</h1>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={`text-xs px-2.5 py-1 rounded ${
                range === r
                  ? "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
                  : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-4 gap-4 mb-8">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-24 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse" />
          ))}
        </div>
      ) : ws ? (
        <>
          <div className="grid grid-cols-4 gap-4 mb-8">
            {[
              { label: t("dashboard.totalInvocations"), value: formatNumber(ws.totalCalls) },
              { label: t("dashboard.totalCost"), value: formatUsd(ws.totalCostUsd) },
              { label: t("dashboard.totalInputTokens"), value: formatNumber(ws.totalInputTokens) },
              { label: t("dashboard.totalOutputTokens"), value: formatNumber(ws.totalOutputTokens) },
            ].map((card) => (
              <div key={card.label} className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3">
                <p className="text-xs text-gray-500 dark:text-gray-400">{card.label}</p>
                <p className="text-2xl font-semibold text-gray-900 dark:text-gray-100 mt-1">{card.value}</p>
              </div>
            ))}
          </div>

          {agents.length === 0 ? (
            <p className="text-center text-gray-400 dark:text-gray-500 py-12">{t("dashboard.emptyState")}</p>
          ) : (
            <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 dark:bg-gray-800 text-left text-xs text-gray-500 dark:text-gray-400">
                    <th className="px-4 py-2.5">{t("dashboard.agentName")}</th>
                    <th className="px-4 py-2.5 text-right">{t("dashboard.calls")}</th>
                    <th className="px-4 py-2.5 text-right">{t("dashboard.cost")}</th>
                    <th className="px-4 py-2.5 text-right">{t("dashboard.totalInputTokens")}</th>
                    <th className="px-4 py-2.5 text-right">{t("dashboard.totalOutputTokens")}</th>
                  </tr>
                </thead>
                <tbody>
                  {agents
                    .sort((a, b) => b.costUsd - a.costUsd)
                    .map((a) => (
                      <tr key={a.agentId} className="border-t border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50">
                        <td className="px-4 py-2.5 text-gray-900 dark:text-gray-100 font-medium">{a.name || a.agentId}</td>
                        <td className="px-4 py-2.5 text-right text-gray-600 dark:text-gray-300">{formatNumber(a.calls)}</td>
                        <td className="px-4 py-2.5 text-right text-gray-600 dark:text-gray-300">{formatUsd(a.costUsd)}</td>
                        <td className="px-4 py-2.5 text-right text-gray-600 dark:text-gray-300">{formatNumber(a.inputTokens)}</td>
                        <td className="px-4 py-2.5 text-right text-gray-600 dark:text-gray-300">{formatNumber(a.outputTokens)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : (
        <p className="text-center text-red-400 py-12">Failed to load dashboard data</p>
      )}
    </div>
  );
}
