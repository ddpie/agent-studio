import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useTraceStats } from "../../hooks/useTraces";
import type { TraceStats, TraceStatsRange } from "../../lib/api-client";

function formatMs(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${Math.round(v)}ms`;
}

function formatPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/**
 * Inline SVG sparkline — p95 latency per bucket with an error overlay.
 * Kept tiny and dependency-free.
 */
function Sparkline({ stats }: { stats: TraceStats }) {
  const buckets = stats.timeseries;
  if (!buckets || buckets.length === 0) {
    return (
      <div className="text-[10px] text-gray-400 dark:text-gray-500">—</div>
    );
  }
  const w = 160;
  const h = 28;
  const pad = 2;
  const values = buckets.map((b) => b.p95Ms ?? 0);
  const counts = buckets.map((b) => b.count);
  const maxV = Math.max(1, ...values);
  const maxC = Math.max(1, ...counts);
  const step = buckets.length > 1 ? (w - pad * 2) / (buckets.length - 1) : 0;

  const points = values
    .map((v, i) => {
      const x = pad + i * step;
      const y = h - pad - ((v / maxV) * (h - pad * 2));
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className="w-full h-7"
      preserveAspectRatio="none"
      role="img"
      aria-label="p95 latency sparkline"
    >
      {/* count bars (faint) */}
      {buckets.map((b, i) => {
        const x = pad + i * step - Math.max(1, step * 0.3);
        const barW = Math.max(1, step * 0.6);
        const barH = (b.count / maxC) * (h - pad * 2);
        const y = h - pad - barH;
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={barW}
            height={barH}
            className="fill-gray-200 dark:fill-gray-700"
            opacity={0.5}
          />
        );
      })}
      {/* p95 line */}
      {values.length > 1 && (
        <polyline
          points={points}
          fill="none"
          className="stroke-blue-500 dark:stroke-blue-400"
          strokeWidth={1.25}
        />
      )}
      {/* error dots */}
      {buckets.map((b, i) => {
        if (!b.errors) return null;
        const x = pad + i * step;
        const y = h - pad - (((b.p95Ms ?? 0) / maxV) * (h - pad * 2));
        return (
          <circle
            key={`e-${i}`}
            cx={x}
            cy={y}
            r={1.75}
            className="fill-red-500 dark:fill-red-400"
          />
        );
      })}
    </svg>
  );
}

function StatCard({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "error";
}) {
  const toneCls =
    tone === "error"
      ? "text-red-600 dark:text-red-400"
      : "text-gray-900 dark:text-gray-100";
  return (
    <div className="flex-1 min-w-0 px-3 py-2 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
      <div className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
        {label}
      </div>
      <div className={`text-sm font-semibold mt-0.5 truncate ${toneCls}`}>{value}</div>
    </div>
  );
}

export default function TraceStatsStrip({
  agentId,
  range,
  onRangeChange,
}: {
  agentId: string;
  range: TraceStatsRange;
  onRangeChange: (r: TraceStatsRange) => void;
}) {
  const { t } = useTranslation();
  const { stats, error, loading } = useTraceStats(agentId, range);

  const count = stats?.count ?? 0;
  const errs = stats?.errorCount ?? 0;
  const rate = stats?.errorRate ?? 0;
  const p95 = stats?.latencyMs.p95 ?? null;
  const avg = stats?.latencyMs.avg ?? null;
  const p50 = stats?.latencyMs.p50 ?? null;
  const p99 = stats?.latencyMs.p99 ?? null;

  return (
    <div className="mb-3" data-testid="trace-stats-strip">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-semibold text-gray-700 dark:text-gray-200">
          {t("traces.stats.title")}
        </div>
        <div className="flex items-center gap-2">
          <div
            role="tablist"
            aria-label={t("traces.stats.rangeLabel")}
            className="inline-flex rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 overflow-hidden"
          >
            {(["24h", "7d"] as TraceStatsRange[]).map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={range === r}
                data-testid={`trace-range-${r}`}
                onClick={() => onRangeChange(r)}
                className={`px-2 py-0.5 text-[11px] ${
                  range === r
                    ? "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200"
                    : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
                }`}
              >
                {t(`traces.stats.range.${r}`)}
              </button>
            ))}
          </div>
          {loading && (
            <RefreshCw className="w-3 h-3 animate-spin text-gray-400" aria-hidden />
          )}
        </div>
      </div>

      {error && (
        <div className="text-xs text-red-600 mb-2">
          {t("traces.stats.loadError")}: {error.message}
        </div>
      )}

      <div className="flex gap-2">
        <StatCard
          label={t("traces.stats.requests")}
          value={count.toLocaleString()}
        />
        <StatCard
          label={t("traces.stats.errors")}
          value={`${errs.toLocaleString()} (${formatPct(rate)})`}
          tone={errs > 0 ? "error" : "default"}
        />
        <StatCard label={t("traces.stats.p95")} value={formatMs(p95)} />
        <StatCard label={t("traces.stats.avg")} value={formatMs(avg)} />
      </div>

      <div className="mt-2 px-3 py-1.5 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <div className="flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400 mb-0.5">
          <span>{t("traces.stats.sparklineLabel")}</span>
          <span className="font-mono">
            p50 {formatMs(p50)} · p99 {formatMs(p99)}
          </span>
        </div>
        {stats ? (
          <Sparkline stats={stats} />
        ) : (
          <div className="h-7" aria-hidden />
        )}
      </div>
    </div>
  );
}
