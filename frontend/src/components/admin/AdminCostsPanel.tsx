import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";
import {
  fetchAdminCosts,
  type AdminCostsResponse,
  type CostRange,
} from "../../lib/api-client";

const RANGES: CostRange[] = ["24h", "7d", "30d"];

function formatUsd(v: number): string {
  if (v >= 1) return `$${v.toFixed(2)}`;
  if (v > 0) return `$${v.toFixed(4)}`;
  return "$0.00";
}

function formatNumber(n: number): string {
  return new Intl.NumberFormat().format(Math.round(n || 0));
}

export default function AdminCostsPanel() {
  const { t } = useTranslation();
  const [range, setRange] = useState<CostRange>("7d");
  const [data, setData] = useState<AdminCostsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAdminCosts(range)
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
  }, [range, tick]);

  const workspaces = useMemo(
    () =>
      [...(data?.workspaces ?? [])].sort(
        (a, b) => b.costUsd - a.costUsd || b.calls - a.calls,
      ),
    [data?.workspaces],
  );

  const topAgents = useMemo(
    () =>
      [...(data?.agents ?? [])]
        .sort((a, b) => b.costUsd - a.costUsd || b.calls - a.calls)
        .slice(0, 20),
    [data?.agents],
  );

  const totals = data?.totals;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-700 pb-3 mb-4">
        <div>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {t("admin.costs.subtitle", {
              defaultValue:
                "Cross-workspace cost rollup. Same Logs Insights scan cost as the per-workspace page (Insights bills by bytes scanned, not by groupby cardinality).",
            })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="inline-flex overflow-hidden rounded-lg border border-gray-300 text-xs dark:border-gray-600"
            role="tablist"
          >
            {RANGES.map((r) => (
              <button
                key={r}
                role="tab"
                aria-selected={range === r}
                onClick={() => setRange(r)}
                className={`px-2 py-1 ${
                  range === r
                    ? "bg-blue-600 text-white"
                    : "bg-white text-gray-700 hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          <button
            onClick={() => setTick((t) => t + 1)}
            className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
            aria-label={t("costs.refresh", { defaultValue: "Refresh" })}
          >
            <RefreshCw className="h-3 w-3" />
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-900/40 dark:bg-red-950/30 dark:text-red-300">
          {error.message}
        </div>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
        </div>
      )}

      {data && (
        <div className="flex-1 overflow-y-auto space-y-6">
          {/* Totals */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label={t("admin.costs.totalCost", { defaultValue: "Total cost" })} value={formatUsd(totals?.costUsd ?? 0)} />
            <Stat label={t("admin.costs.totalCalls", { defaultValue: "Calls" })} value={formatNumber(totals?.calls ?? 0)} />
            <Stat label={t("admin.costs.totalInput", { defaultValue: "Input tokens" })} value={formatNumber(totals?.inputTokens ?? 0)} />
            <Stat label={t("admin.costs.totalOutput", { defaultValue: "Output tokens" })} value={formatNumber(totals?.outputTokens ?? 0)} />
          </div>

          {/* Workspaces */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
              {t("admin.costs.byWorkspace", { defaultValue: "By workspace" })}
            </h3>
            <div className="rounded-md border border-gray-200 dark:border-gray-700 overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 dark:bg-gray-900 text-gray-600 dark:text-gray-300">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">{t("admin.costs.workspace", { defaultValue: "Workspace" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.agents", { defaultValue: "Agents" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.calls", { defaultValue: "Calls" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.inputShort", { defaultValue: "In tok" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.outputShort", { defaultValue: "Out tok" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.cost", { defaultValue: "Cost" })}</th>
                  </tr>
                </thead>
                <tbody>
                  {workspaces.map((w) => (
                    <tr key={w.workspaceId} className="border-t border-gray-100 dark:border-gray-800">
                      <td className="px-3 py-2 text-gray-900 dark:text-gray-100">
                        <div className="flex flex-col">
                          <span>{w.name}</span>
                          <span className="text-[10px] font-mono text-gray-400 dark:text-gray-500">
                            {w.workspaceId}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-300">{formatNumber(w.agentCount)}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-300">{formatNumber(w.calls)}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-300">{formatNumber(w.inputTokens)}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-300">{formatNumber(w.outputTokens)}</td>
                      <td className="px-3 py-2 text-right font-mono text-gray-900 dark:text-gray-100">{formatUsd(w.costUsd)}</td>
                    </tr>
                  ))}
                  {workspaces.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-3 py-4 text-center text-gray-500 dark:text-gray-400">
                        {t("costs.empty", { defaultValue: "No data" })}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          {/* Top agents */}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">
              {t("admin.costs.topAgents", { defaultValue: "Top agents" })}
            </h3>
            <div className="rounded-md border border-gray-200 dark:border-gray-700 overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 dark:bg-gray-900 text-gray-600 dark:text-gray-300">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">{t("admin.costs.agent", { defaultValue: "Agent" })}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("admin.costs.workspace", { defaultValue: "Workspace" })}</th>
                    <th className="text-left px-3 py-2 font-medium">{t("admin.costs.model", { defaultValue: "Model" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.calls", { defaultValue: "Calls" })}</th>
                    <th className="text-right px-3 py-2 font-medium">{t("admin.costs.cost", { defaultValue: "Cost" })}</th>
                  </tr>
                </thead>
                <tbody>
                  {topAgents.map((a) => (
                    <tr key={a.agentId} className="border-t border-gray-100 dark:border-gray-800">
                      <td className="px-3 py-2 text-gray-900 dark:text-gray-100">{a.name}</td>
                      <td className="px-3 py-2 font-mono text-[10px] text-gray-500 dark:text-gray-400">{a.workspaceId}</td>
                      <td className="px-3 py-2 font-mono text-[10px] text-gray-500 dark:text-gray-400">{a.modelId || "—"}</td>
                      <td className="px-3 py-2 text-right text-gray-600 dark:text-gray-300">{formatNumber(a.calls)}</td>
                      <td className="px-3 py-2 text-right font-mono text-gray-900 dark:text-gray-100">{formatUsd(a.costUsd)}</td>
                    </tr>
                  ))}
                  {topAgents.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-4 text-center text-gray-500 dark:text-gray-400">
                        {t("costs.empty", { defaultValue: "No data" })}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
      <div className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">{label}</div>
      <div className="mt-0.5 font-mono text-sm text-gray-900 dark:text-gray-100">{value}</div>
    </div>
  );
}
