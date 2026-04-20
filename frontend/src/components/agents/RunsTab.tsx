import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, Calendar, User } from "lucide-react";
import { useRunList, useRunDetail } from "../../hooks/useRuns";
import RunDetail from "./RunDetail";
import TraceStatsStrip from "./TraceStatsStrip";
import { formatDateTime } from "../../lib/date-format";
import type { RunSummary } from "../../lib/runs-client";
import type { TraceStatsRange } from "../../lib/api-client";

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

function RunListItem({ run, selected, onSelect }: { run: RunSummary; selected: boolean; onSelect: () => void }) {
  const isError = run.status === "failed" || run.status === "timeout";
  const suffix = scheduleSuffix(run.scheduleId);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
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
          <span className="text-[10px] text-gray-500 dark:text-gray-400 whitespace-nowrap">
            {formatRelativeTime(run.startedAt)}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-[10px] text-gray-500 dark:text-gray-400">
          <span className={`inline-flex items-center gap-1 ${isError ? "text-red-600 dark:text-red-400" : ""}`}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${isError ? "bg-red-500" : run.status === "running" ? "bg-amber-500" : "bg-emerald-500"}`} />
            {run.status}
          </span>
          {run.durationMs != null && <span>{run.durationMs < 1000 ? `${run.durationMs}ms` : `${(run.durationMs / 1000).toFixed(1)}s`}</span>}
          {run.totalTokens != null && <span>{run.totalTokens < 1000 ? `${run.totalTokens} tok` : `${(run.totalTokens / 1000).toFixed(1)}k tok`}</span>}
          {run.artifactCount > 0 && <span>📎 {run.artifactCount}</span>}
        </div>
        <div className="mt-1 inline-flex items-center gap-1 text-[10px] text-gray-500 dark:text-gray-400">
          {run.trigger === "schedule" ? (
            <>
              <Calendar className="w-3 h-3" />
              <span>{suffix ? `Schedule · ${suffix}` : "Schedule"}</span>
            </>
          ) : (
            <>
              <User className="w-3 h-3" />
              <span>Manual</span>
            </>
          )}
        </div>
      </button>
    </li>
  );
}

interface Props {
  agentId: string;
  initialRunId?: string | null;
  onSelect?: (runId: string) => void;
}

export default function RunsTab({ agentId, initialRunId, onSelect }: Props) {
  const { t } = useTranslation();
  const runs = useRunList(agentId);
  const [selectedId, setSelectedId] = useState<string | null>(initialRunId ?? null);
  const runDetail = useRunDetail(agentId, selectedId);
  const [range, setRange] = useState<TraceStatsRange>("24h");

  return (
    <div className="p-4 h-full min-h-[400px] flex flex-col" data-testid="runs-tab">
      <TraceStatsStrip agentId={agentId} range={range} onRangeChange={setRange} />
      <div className="flex gap-4 flex-1 min-h-0">
        <div className="w-72 flex-shrink-0">
          <div className="flex items-center justify-between mb-2">
            <div>
              <h3 className="text-sm font-semibold">{t("runs.tab")}</h3>
              <p className="text-xs text-gray-500 dark:text-gray-400">{t("runs.subtitle")}</p>
            </div>
            <button
              type="button"
              onClick={runs.refresh}
              disabled={runs.loading}
              className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
            >
              <RefreshCw className={`w-3 h-3 ${runs.loading ? "animate-spin" : ""}`} />
            </button>
          </div>
          {runs.error && <div className="text-xs text-red-600 dark:text-red-400">{runs.error.message}</div>}
          {runs.runs && runs.runs.length === 0 && !runs.loading && (
            <div className="text-xs">
              <div className="font-medium">{t("runs.empty")}</div>
              <div className="text-gray-500 dark:text-gray-400 mt-1">{t("runs.emptyHint")}</div>
            </div>
          )}
          {runs.runs && runs.runs.length > 0 && (
            <ul className="space-y-1" data-testid="runs-list">
              {runs.runs.map((r) => (
                <RunListItem
                  key={r.runId}
                  run={r}
                  selected={selectedId === r.runId}
                  onSelect={() => {
                    setSelectedId(r.runId);
                    onSelect?.(r.runId);
                  }}
                />
              ))}
            </ul>
          )}
        </div>

        <div className="flex-1 min-w-0 overflow-auto">
          {!selectedId && <div className="text-sm text-gray-500 dark:text-gray-400">{t("runs.selectHint")}</div>}
          {selectedId && runDetail.loading && !runDetail.detail && (
            <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
          )}
          {selectedId && runDetail.error && (
            <div className="text-sm text-red-600 dark:text-red-400">{runDetail.error.message}</div>
          )}
          {selectedId && runDetail.detail && (
            <RunDetail agentId={agentId} detail={runDetail.detail} output={runDetail.output} loading={runDetail.loading} />
          )}
        </div>
      </div>
    </div>
  );
}
