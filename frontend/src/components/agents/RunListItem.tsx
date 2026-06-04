import { useTranslation } from "react-i18next";
import { Calendar, User } from "lucide-react";
import type { RunSummary } from "../../lib/runs-client";

function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso.endsWith("Z") || /[+\-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + "Z").getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

function scheduleSuffix(scheduleId: string | null): string | null {
  if (!scheduleId) return null;
  const prefix = "agent-studio-";
  const name = scheduleId.startsWith(prefix) ? scheduleId.slice(prefix.length) : scheduleId;
  const dashIdx = name.indexOf("-", name.indexOf("-") + 1);
  return dashIdx > 0 ? name.slice(dashIdx + 1) : name;
}

/**
 * Compact card representation of one run. Used in RunsTab and in the
 * Schedules expanded-row recent-runs list — same shape in both so users
 * get consistent status/duration/token badges.
 */
export default function RunListItem({
  run,
  selected,
  onSelect,
  hideSource = false,
}: {
  run: RunSummary;
  selected: boolean;
  onSelect: () => void;
  /** Hide the trigger-source line (the Schedules tab already scopes to one schedule). */
  hideSource?: boolean;
}) {
  const { t } = useTranslation();
  const isError = run.status === "failed" || run.status === "timeout";
  const suffix = scheduleSuffix(run.scheduleId);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={`run-card-${run.runId}`}
        className={`w-full text-left px-2.5 py-2 rounded-md transition-colors ${
          selected
            ? "bg-blue-50 dark:bg-blue-900/30 ring-1 ring-blue-300 dark:ring-blue-800"
            : "hover:bg-gray-100 dark:hover:bg-gray-800/60"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
            {truncate(run.input || run.runId, 60)}
          </span>
          <span className="text-[10px] text-gray-600 dark:text-gray-300 whitespace-nowrap">
            {formatRelativeTime(run.startedAt)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-[10px] text-gray-600 dark:text-gray-300">
          <span className={`inline-flex items-center gap-1 ${isError ? "text-red-600 dark:text-red-400" : ""}`}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${isError ? "bg-red-500" : run.status === "running" ? "bg-amber-500" : "bg-emerald-500"}`} />
            {t(`runs.statusLabel.${run.status}`, run.status)}
          </span>
          {run.durationMs != null && <span>{Number(run.durationMs) < 1000 ? `${Number(run.durationMs)}ms` : `${(Number(run.durationMs) / 1000).toFixed(1)}s`}</span>}
          {run.totalTokens != null && <span>{Number(run.totalTokens) < 1000 ? `${Number(run.totalTokens)} tok` : `${(Number(run.totalTokens) / 1000).toFixed(1)}k tok`}</span>}
          {run.artifactCount > 0 && <span>📎 {run.artifactCount}</span>}
        </div>
        {!hideSource && (
          <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-gray-600 dark:text-gray-300">
            {run.trigger === "schedule" ? (
              <>
                <Calendar className="w-3 h-3" />
                <span>{suffix ? `${t("runs.source.scheduled")} · ${suffix}` : t("runs.source.scheduled")}</span>
              </>
            ) : (
              <>
                <User className="w-3 h-3" />
                <span>{t("runs.source.manual")}</span>
              </>
            )}
          </div>
        )}
      </button>
    </li>
  );
}
