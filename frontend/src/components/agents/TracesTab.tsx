import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, ChevronDown, ChevronRight } from "lucide-react";
import { useTraceSessions, useSessionTrace, useSessionOutput } from "../../hooks/useTraces";
import SpanTree from "./SpanTree";
import TraceStatsStrip from "./TraceStatsStrip";
import type { TraceStatsRange, TraceSummary } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date-format";

interface Props {
  agentId: string;
  initialSessionId?: string | null;
}

function Metric({ label, value, mono }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div className="bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded px-2.5 py-1.5">
      <div className="text-[10px] text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</div>
      <div className={`text-xs text-gray-800 dark:text-gray-200 mt-0.5 truncate ${mono ? "font-mono" : ""}`} title={value || ""}>{value || "—"}</div>
    </div>
  );
}

function formatRelativeTime(iso?: string): string {
  if (!iso) return "";
  // Backend may emit UTC without a tz suffix (e.g. CWL Insights). Normalise
  // so the relative delta isn't off by the viewer's tz offset.
  const normalized = /[zZ]$|[+\-]\d{2}:?\d{2}$/.test(iso) ? iso : iso.replace(" ", "T") + "Z";
  const then = new Date(normalized).getTime();
  if (Number.isNaN(then)) return iso;
  const delta = Math.max(0, Date.now() - then);
  const sec = Math.round(delta / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.round(hr / 24);
  return `${d}d ago`;
}

function formatTokens(n?: number | null): string {
  if (n == null) return "";
  if (n < 1000) return `${n} tok`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k tok`;
  return `${(n / 1_000_000).toFixed(1)}M tok`;
}

function formatLatency(ms?: number | null): string {
  if (ms == null) return "";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function sessionLabel(sessionId: string): string {
  if (sessionId.startsWith("sched-")) {
    // `sched-<suffix>-...` — show suffix up to the next `-manual-` or ISO date
    const rest = sessionId.slice("sched-".length);
    const stop = rest.search(/-(?:manual-|\d{4}-\d{2}-\d{2})/);
    const suffix = stop > 0 ? rest.slice(0, stop) : rest;
    return `Schedule · ${suffix}`;
  }
  return sessionId;
}

function SessionListItem({
  session,
  selected,
  onSelect,
}: {
  session: TraceSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const isError = (session.status || "").toUpperCase() === "ERROR";
  const when = formatRelativeTime(session.firstEvent);
  const absTime = session.firstEvent
    ? formatDateTime(session.firstEvent, session.sessionId)
    : session.sessionId;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={`session-row-${session.sessionId}`}
        title={`${session.sessionId}\n${absTime}`}
        className={`w-full text-left px-2.5 py-2 rounded-md transition-colors ${
          selected
            ? "bg-blue-50 dark:bg-blue-900/30 ring-1 ring-blue-300 dark:ring-blue-800"
            : "hover:bg-gray-100 dark:hover:bg-gray-800/60"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
            {sessionLabel(session.sessionId)}
          </span>
          <span className="text-[10px] text-gray-500 dark:text-gray-400 whitespace-nowrap">
            {when}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-1 text-[10px] text-gray-500 dark:text-gray-400">
          <span className={`inline-flex items-center gap-1 ${isError ? "text-red-600 dark:text-red-400" : ""}`}>
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${isError ? "bg-red-500" : "bg-emerald-500"}`} />
            {isError ? "error" : "ok"}
          </span>
          {session.durationMs != null && <span>{formatLatency(session.durationMs)}</span>}
          {session.totalTokens != null && <span>{formatTokens(session.totalTokens)}</span>}
          <span>{session.spanCount} spans</span>
        </div>
      </button>
    </li>
  );
}

export default function TracesTab({ agentId, initialSessionId }: Props) {
  const { t } = useTranslation();
  const sessions = useTraceSessions(agentId);
  const [selected, setSelected] = useState<string | null>(initialSessionId ?? null);
  const trace = useSessionTrace(agentId, selected);
  const output = useSessionOutput(agentId, selected);
  const [showSpans, setShowSpans] = useState(false);
  const [range, setRange] = useState<TraceStatsRange>("24h");

  useEffect(() => {
    if (initialSessionId) {
      setSelected(initialSessionId);
    }
  }, [initialSessionId]);

  return (
    <div className="p-4 h-full min-h-[400px] flex flex-col" data-testid="traces-tab">
      <TraceStatsStrip agentId={agentId} range={range} onRangeChange={setRange} />
      <div className="flex gap-4 flex-1 min-h-0">
      <div className="w-72 flex-shrink-0">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h3 className="text-sm font-semibold">{t("traces.tab")}</h3>
            <p className="text-xs text-gray-500 dark:text-gray-400">{t("traces.subtitle")}</p>
          </div>
          <button
            type="button"
            onClick={sessions.refresh}
            disabled={sessions.loading}
            className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
            aria-label={t("common.refresh")}
          >
            <RefreshCw className={`w-3 h-3 ${sessions.loading ? "animate-spin" : ""}`} />
          </button>
        </div>
        {sessions.error && (
          <div className="text-xs text-red-600 dark:text-red-400">
            {t("traces.loadError")}: {sessions.error.message}
          </div>
        )}
        {sessions.sessions && sessions.sessions.length === 0 && !sessions.loading && (
          <div className="text-xs">
            <div className="font-medium">{t("traces.emptyTitle")}</div>
            <div className="text-gray-500 dark:text-gray-400 mt-1">{t("traces.emptyHint")}</div>
          </div>
        )}
        {sessions.sessions && sessions.sessions.length > 0 && (
          <ul className="space-y-1" data-testid="sessions-list">
            {sessions.sessions.map((s) => (
              <SessionListItem
                key={s.sessionId}
                session={s}
                selected={selected === s.sessionId}
                onSelect={() => setSelected(s.sessionId)}
              />
            ))}
          </ul>
        )}
      </div>

      <div className="flex-1 min-w-0 overflow-auto">
        {!selected && (
          <div className="text-sm text-gray-500 dark:text-gray-400">{t("traces.selectSessionHint")}</div>
        )}
        {selected && !output.data && trace.loading && !trace.pending && (
          <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
        )}
        {selected && !output.data && trace.pending && !trace.root && (
          <div className="text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
            <RefreshCw className="w-3 h-3 animate-spin" />
            {t("traces.pendingSpans")}
          </div>
        )}
        {selected && trace.error && !output.data && (
          <div className="text-sm text-red-600 dark:text-red-400">
            {t("traces.loadError")}: {trace.error.message}
          </div>
        )}
        {selected && output.data && (
          <div className="mb-4 space-y-3">
            <div className="grid grid-cols-4 gap-2 text-xs">
              <Metric
                label={t("traces.output.model")}
                value={output.data.metrics.model ? output.data.metrics.model.split("/").pop()?.replace(/^us\.|^global\./, "") ?? "—" : "—"}
                mono
              />
              <Metric
                label={t("traces.output.tokens")}
                value={output.data.metrics.totalTokens != null ? `${output.data.metrics.inputTokens ?? 0}→${output.data.metrics.outputTokens ?? 0}` : "—"}
                mono
              />
              <Metric
                label={t("traces.output.latency")}
                value={output.data.metrics.durationMs != null ? `${(output.data.metrics.durationMs / 1000).toFixed(2)}s` : "—"}
              />
              <Metric
                label={t("traces.output.status")}
                value={output.data.metrics.status || "OK"}
              />
            </div>
            {output.data.hasOutput ? (
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
                  {t("traces.output.responseLabel")}
                </div>
                <pre className="text-xs whitespace-pre-wrap bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg p-3 max-h-[50vh] overflow-auto text-gray-800 dark:text-gray-200 leading-relaxed">
                  {output.data.output}
                </pre>
              </div>
            ) : (
              <div className="text-xs text-gray-500 dark:text-gray-400 italic">
                {t("traces.output.noOutput")}
              </div>
            )}
            <button
              type="button"
              onClick={() => setShowSpans((v) => !v)}
              className="text-[11px] text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 inline-flex items-center gap-1"
              data-testid="toggle-span-tree"
            >
              {showSpans ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
              {t("traces.output.technicalDetails")}
            </button>
          </div>
        )}
        {selected && showSpans && trace.root && <SpanTree root={trace.root} />}
      </div>
      </div>
    </div>
  );
}
