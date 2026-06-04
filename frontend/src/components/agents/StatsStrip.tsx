import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getTraceStats, type TraceStats } from "../../lib/api-client";
import Sparkline from "../ui/Sparkline";

interface Props {
  agentId: string;
  range: string;
}

function formatMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function StatCard({
  label,
  value,
  spark,
  alert,
}: {
  label: string;
  value: string;
  spark?: number[];
  alert?: boolean;
}) {
  return (
    <div className="flex-1 min-w-0 bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3">
      <p className="text-xs text-gray-600 dark:text-gray-300 truncate">{label}</p>
      <p
        className={`text-xl font-semibold mt-0.5 ${
          alert ? "text-red-600 dark:text-red-400" : "text-gray-900 dark:text-gray-100"
        }`}
      >
        {value}
      </p>
      {spark && spark.length > 1 && (
        <Sparkline data={spark} className="mt-1 text-gray-400 dark:text-gray-500" />
      )}
    </div>
  );
}

export default function StatsStrip({ agentId, range }: Props) {
  const { t } = useTranslation();
  const [stats, setStats] = useState<TraceStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getTraceStats(agentId, range)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
  }, [agentId, range]);

  if (loading) {
    return (
      <div className="flex gap-3 mb-6">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="flex-1 h-20 bg-gray-100 dark:bg-gray-800 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (!stats) return null;

  const countSpark = stats.timeseries.map((b) => b.count);
  const p95Spark = stats.timeseries.map((b) => b.p95Ms ?? 0);

  return (
    <div className="flex gap-3 mb-6">
      <StatCard label={t("stats.invocations")} value={String(stats.count)} spark={countSpark} />
      <StatCard
        label={t("stats.errorRate")}
        value={formatRate(stats.errorRate)}
        alert={stats.errorRate > 0.05}
      />
      <StatCard label={t("stats.p95Latency")} value={formatMs(stats.latencyMs.p95)} spark={p95Spark} />
      <StatCard label={t("stats.avgLatency")} value={formatMs(stats.latencyMs.avg)} />
    </div>
  );
}
