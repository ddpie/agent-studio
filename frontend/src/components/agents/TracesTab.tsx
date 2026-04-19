import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { useTraceSessions, useSessionTrace } from "../../hooks/useTraces";
import SpanTree from "./SpanTree";
import TraceStatsStrip from "./TraceStatsStrip";
import type { TraceStatsRange } from "../../lib/api-client";

export default function TracesTab({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const sessions = useTraceSessions(agentId);
  const [selected, setSelected] = useState<string | null>(null);
  const trace = useSessionTrace(agentId, selected);
  const [range, setRange] = useState<TraceStatsRange>("24h");

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
            className="text-xs text-gray-500 hover:text-gray-700"
            aria-label={t("common.refresh")}
          >
            <RefreshCw className={`w-3 h-3 ${sessions.loading ? "animate-spin" : ""}`} />
          </button>
        </div>
        {sessions.error && (
          <div className="text-xs text-red-600">
            {t("traces.loadError")}: {sessions.error.message}
          </div>
        )}
        {sessions.sessions && sessions.sessions.length === 0 && !sessions.loading && (
          <div className="text-xs">
            <div className="font-medium">{t("traces.emptyTitle")}</div>
            <div className="text-gray-500 mt-1">{t("traces.emptyHint")}</div>
          </div>
        )}
        {sessions.sessions && sessions.sessions.length > 0 && (
          <ul className="space-y-1" data-testid="sessions-list">
            {sessions.sessions.map((s) => (
              <li key={s.sessionId}>
                <button
                  type="button"
                  onClick={() => setSelected(s.sessionId)}
                  data-testid={`session-row-${s.sessionId}`}
                  className={`w-full text-left px-2 py-1.5 rounded text-xs ${
                    selected === s.sessionId
                      ? "bg-blue-100 dark:bg-blue-900/40"
                      : "hover:bg-gray-100 dark:hover:bg-gray-800"
                  }`}
                >
                  <div className="font-mono truncate">{s.sessionId}</div>
                  <div className="text-gray-500 flex items-center gap-2 mt-0.5">
                    <span>{s.spanCount} spans</span>
                    {s.firstEvent && <span className="truncate">{s.firstEvent}</span>}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex-1 min-w-0 overflow-auto">
        {!selected && (
          <div className="text-sm text-gray-500">{t("traces.selectSessionHint")}</div>
        )}
        {selected && trace.loading && (
          <div className="text-sm text-gray-500">Loading…</div>
        )}
        {selected && trace.error && (
          <div className="text-sm text-red-600">
            {t("traces.loadError")}: {trace.error.message}
          </div>
        )}
        {selected && trace.root && <SpanTree root={trace.root} />}
      </div>
      </div>
    </div>
  );
}
