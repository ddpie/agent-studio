import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { DollarSign, Loader2, RefreshCw } from "lucide-react";
import {
  fetchWorkspaceCosts,
  type CostAgentRow,
  type CostRange,
  type WorkspaceCostsResponse,
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

function AgentCostBar({ agents, total }: { agents: CostAgentRow[]; total: number }) {
  const { t } = useTranslation();
  // Only show priced agents in the bar; fall back to calls as size hint when
  // nothing is priced.
  const fallbackByCalls = total <= 0;
  const denom = fallbackByCalls
    ? agents.reduce((acc, a) => acc + a.calls, 0) || 1
    : total || 1;

  const palette = [
    "bg-blue-500", "bg-emerald-500", "bg-violet-500", "bg-amber-500",
    "bg-rose-500", "bg-cyan-500", "bg-lime-500", "bg-fuchsia-500",
  ];
  const rows = agents
    .filter((a) => (fallbackByCalls ? a.calls > 0 : a.costUsd > 0))
    .slice(0, 8);

  if (rows.length === 0) {
    return (
      <div className="text-sm text-gray-500 dark:text-gray-400">
        {t("costs.empty")}
      </div>
    );
  }

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
        {rows.map((a, idx) => {
          const size = fallbackByCalls ? a.calls : a.costUsd;
          const pct = Math.max(1, (size / denom) * 100);
          return (
            <div
              key={a.agentId}
              className={palette[idx % palette.length]}
              style={{ width: `${pct}%` }}
              title={`${a.name}: ${formatUsd(a.costUsd)} (${formatNumber(a.calls)} ${t("costs.callsShort")})`}
              data-testid={`cost-bar-seg-${a.agentId}`}
            />
          );
        })}
      </div>
      <ul className="mt-3 grid grid-cols-1 gap-1 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map((a, idx) => (
          <li key={a.agentId} className="flex items-center gap-2 text-xs">
            <span className={`h-2 w-2 rounded-full ${palette[idx % palette.length]}`} />
            <span className="truncate text-gray-700 dark:text-gray-300">{a.name}</span>
            {a.status === "archived" && (
              <span className="rounded-sm bg-gray-200 px-1 py-[1px] text-[10px] uppercase tracking-wide text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                {t("costs.archivedBadge")}
              </span>
            )}
            <span className="ml-auto font-mono text-gray-900 dark:text-gray-100">{formatUsd(a.costUsd)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Sparkline({ points }: { points: { bucket: string; costUsd: number; calls: number }[] }) {
  const w = 600;
  const h = 80;
  const pad = 4;
  const n = points.length;

  if (n === 0) {
    return null;
  }

  const maxCost = points.reduce((m, p) => Math.max(m, p.costUsd), 0);
  const maxCalls = points.reduce((m, p) => Math.max(m, p.calls), 0);
  // Prefer cost series when there's priced data; otherwise fall back to calls.
  const useCost = maxCost > 0;
  const series = points.map((p) => (useCost ? p.costUsd : p.calls));
  const max = useCost ? maxCost : (maxCalls || 1);

  const stepX = (w - pad * 2) / Math.max(1, n - 1);
  const path = series
    .map((v, i) => {
      const x = pad + i * stepX;
      const y = h - pad - (v / max) * (h - pad * 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const areaPath =
    path +
    ` L${(pad + (n - 1) * stepX).toFixed(1)},${(h - pad).toFixed(1)} L${pad},${(h - pad).toFixed(1)} Z`;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-20 w-full text-blue-500 dark:text-blue-400"
      aria-label="cost timeseries"
    >
      <path d={areaPath} fill="currentColor" fillOpacity="0.12" />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
      {series.map((v, i) => {
        const x = pad + i * stepX;
        const y = h - pad - (v / max) * (h - pad * 2);
        return <circle key={i} cx={x} cy={y} r="1.5" fill="currentColor" />;
      })}
    </svg>
  );
}

export default function CostsPage() {
  const { t } = useTranslation();
  const [range, setRange] = useState<CostRange>("7d");
  const [data, setData] = useState<WorkspaceCostsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchWorkspaceCosts(range)
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

  const agents = data?.agents ?? [];
  const ws = data?.workspace;

  const sortedAgents = useMemo(
    () => [...agents].sort((a, b) => b.costUsd - a.costUsd || b.calls - a.calls),
    [agents],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {t("costs.title")}
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("costs.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="inline-flex overflow-hidden rounded-lg border border-gray-300 text-xs dark:border-gray-600"
            role="tablist"
          >
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={range === r}
                onClick={() => setRange(r)}
                data-testid={`costs-range-${r}`}
                className={`px-3 py-1.5 ${
                  range === r
                    ? "bg-blue-500 text-white"
                    : "bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
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
            className="rounded p-1.5 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
            aria-label={t("common.refresh")}
            data-testid="costs-refresh"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6" data-testid="costs-content">
        <div
          className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-600 dark:bg-amber-900/20 dark:text-amber-200"
          data-testid="costs-disclaimer"
        >
          {t("costs.disclaimer")}
        </div>

        {error && (
          <div
            className="mb-4 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-600 dark:bg-red-900/20 dark:text-red-200"
            data-testid="costs-error"
          >
            {t("costs.loadError")}: {error.message}
          </div>
        )}

        {loading && !data && (
          <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t("common.loading")}
          </div>
        )}

        {ws && (
          <>
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div
                className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30 p-4"
                data-testid="costs-total-card"
              >
                <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <DollarSign className="h-3.5 w-3.5" />
                  {t("costs.totalCost")}
                </div>
                <div className="mt-1 font-mono text-2xl font-semibold text-gray-900 dark:text-gray-100">
                  {formatUsd(ws.totalCostUsd)}
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30 p-4">
                <div className="text-xs text-gray-500 dark:text-gray-400">{t("costs.totalCalls")}</div>
                <div className="mt-1 font-mono text-2xl font-semibold text-gray-900 dark:text-gray-100">
                  {formatNumber(ws.totalCalls)}
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30 p-4">
                <div className="text-xs text-gray-500 dark:text-gray-400">{t("costs.totalTokens")}</div>
                <div className="mt-1 font-mono text-2xl font-semibold text-gray-900 dark:text-gray-100">
                  {formatNumber(ws.totalInputTokens + ws.totalOutputTokens)}
                </div>
                <div className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
                  in {formatNumber(ws.totalInputTokens)} / out {formatNumber(ws.totalOutputTokens)}
                </div>
              </div>
            </div>

            <section className="mb-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30 p-4">
              <h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">
                {t("costs.stackedTitle")}
              </h3>
              <AgentCostBar agents={sortedAgents} total={ws.totalCostUsd} />
            </section>

            <section className="mb-6 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30 p-4">
              <h3 className="mb-3 text-sm font-semibold text-gray-800 dark:text-gray-200">
                {t("costs.trendTitle")}
              </h3>
              {ws.timeseries.length === 0 ? (
                <div className="text-sm text-gray-500 dark:text-gray-400">{t("costs.empty")}</div>
              ) : (
                <>
                  <Sparkline points={ws.timeseries} />
                  <div className="mt-1 flex justify-between text-[11px] text-gray-500 dark:text-gray-400">
                    <span>{ws.timeseries[0]?.bucket}</span>
                    <span>{ws.timeseries[ws.timeseries.length - 1]?.bucket}</span>
                  </div>
                </>
              )}
            </section>

            <section className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30">
              <h3 className="border-b border-gray-200 dark:border-gray-700 px-4 py-3 text-sm font-semibold text-gray-800 dark:text-gray-200">
                {t("costs.perAgent")}
              </h3>
              <table className="w-full text-sm" data-testid="costs-table">
                <thead className="bg-gray-50 dark:bg-gray-900/60 text-xs text-gray-500 dark:text-gray-400">
                  <tr>
                    <th className="px-4 py-2 text-left font-normal">{t("costs.col.agent")}</th>
                    <th className="px-4 py-2 text-left font-normal">{t("costs.col.model")}</th>
                    <th className="px-4 py-2 text-right font-normal">{t("costs.col.calls")}</th>
                    <th className="px-4 py-2 text-right font-normal">{t("costs.col.inputTokens")}</th>
                    <th className="px-4 py-2 text-right font-normal">{t("costs.col.outputTokens")}</th>
                    <th className="px-4 py-2 text-right font-normal">{t("costs.col.cost")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {sortedAgents.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-6 text-center text-xs text-gray-500 dark:text-gray-400">
                        {t("costs.empty")}
                      </td>
                    </tr>
                  )}
                  {sortedAgents.map((a) => (
                    <tr
                      key={a.agentId}
                      data-testid={`costs-row-${a.agentId}`}
                      className={a.status === "archived" ? "text-gray-500 dark:text-gray-400" : ""}
                    >
                      <td className="max-w-[260px] px-4 py-2 text-gray-900 dark:text-gray-100">
                        <div className="flex items-center gap-2">
                          <span className="truncate">{a.name}</span>
                          {a.status === "archived" && (
                            <span className="shrink-0 rounded-sm bg-gray-200 px-1 py-[1px] text-[10px] uppercase tracking-wide text-gray-600 dark:bg-gray-700 dark:text-gray-300">
                              {t("costs.archivedBadge")}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="max-w-[200px] truncate px-4 py-2 text-xs text-gray-500 dark:text-gray-400">
                        {a.modelId || "—"}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">{formatNumber(a.calls)}</td>
                      <td className="px-4 py-2 text-right font-mono">{formatNumber(a.inputTokens)}</td>
                      <td className="px-4 py-2 text-right font-mono">{formatNumber(a.outputTokens)}</td>
                      <td className="px-4 py-2 text-right font-mono">{formatUsd(a.costUsd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
