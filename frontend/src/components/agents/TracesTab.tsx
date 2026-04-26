import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, ChevronDown, ChevronRight, CheckCircle2, XCircle } from "lucide-react";
import { listTraces, getSessionTrace, type TraceSession, type TraceDetail } from "../../lib/api-client";
import RunDetail from "./RunDetail";
import type { RunDetail as RunDetailType, RunOutput } from "../../lib/runs-client";

interface Props {
  agentId: string;
  range: string;
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function StatusBadge({ status }: { status: string }) {
  const isError = status === "ERROR";
  return isError ? (
    <XCircle className="w-4 h-4 text-red-500" />
  ) : (
    <CheckCircle2 className="w-4 h-4 text-green-500" />
  );
}

function transformTraceToRunDetail(trace: TraceDetail): { detail: RunDetailType; output: RunOutput } {
  const hasError = trace.summary.errorCount > 0;

  return {
    detail: {
      runId: trace.sessionId,
      trigger: "manual" as const,
      scheduleId: null,
      sessionId: trace.sessionId,
      status: hasError ? "failed" : "completed",
      input: "",
      outputUrl: null,
      artifactRefs: [],
      usage: {
        promptTokens: trace.summary.inputTokens,
        completionTokens: trace.summary.outputTokens,
        totalTokens: trace.summary.inputTokens + trace.summary.outputTokens,
      },
      durationMs: trace.summary.totalDurationMs,
      model: trace.summary.model,
      error: hasError ? { code: "TRACE_ERROR", message: "One or more spans failed" } : null,
      startedAt: new Date(Date.now() - trace.summary.totalDurationMs).toISOString(),
      completedAt: new Date().toISOString(),
    },
    output: {
      text: "",
      toolCalls: trace.summary.toolCalls,
    },
  };
}

export default function TracesTab({ agentId, range }: Props) {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<TraceSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [traceDetail, setTraceDetail] = useState<TraceDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const fetchSessions = async () => {
    setLoading(true);
    try {
      const data = await listTraces(agentId, range);
      setSessions(data);
    } catch (err) {
      console.error("Failed to fetch traces:", err);
      setSessions([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSessions();
  }, [agentId, range]);

  const handleRowClick = async (sessionId: string) => {
    if (expandedId === sessionId) {
      setExpandedId(null);
      setTraceDetail(null);
      return;
    }

    setExpandedId(sessionId);
    setDetailLoading(true);
    try {
      const detail = await getSessionTrace(agentId, sessionId);
      setTraceDetail(detail);
    } catch (err) {
      console.error("Failed to fetch trace detail:", err);
      setTraceDetail(null);
    } finally {
      setDetailLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-gray-500 dark:text-gray-400">
        {t("common.loading") || "Loading..."}
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-sm text-gray-500 dark:text-gray-400">
        <p>{t("traces.empty") || "No trace sessions found"}</p>
        <p className="text-xs mt-1">{t("traces.emptyHint") || "Sessions will appear here after agent invocations"}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">
          {t("traces.title") || "Trace Sessions"}
        </h3>
        <button
          onClick={fetchSessions}
          className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors"
          title={t("common.refresh") || "Refresh"}
        >
          <RefreshCw className="w-4 h-4 text-gray-600 dark:text-gray-400" />
        </button>
      </div>

      <div className="flex-1 overflow-auto">
        <table className="w-full">
          <thead className="sticky top-0 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 z-10">
            <tr>
              <th className="w-8 px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400" />
              <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400">
                {t("traces.session") || "Session"}
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400">
                {t("traces.time") || "Time"}
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400">
                {t("traces.status") || "Status"}
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400">
                {t("traces.duration") || "Duration"}
              </th>
              <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-600 dark:text-gray-400">
                {t("traces.tools") || "Tools"}
              </th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => {
              const isExpanded = expandedId === session.sessionId;
              const transformed = isExpanded && traceDetail
                ? transformTraceToRunDetail(traceDetail)
                : null;

              return (
                <tr key={session.sessionId}>
                  <td colSpan={6} className="border-b border-gray-200 dark:border-gray-800">
                    <button
                      onClick={() => handleRowClick(session.sessionId)}
                      className="w-full hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
                    >
                      <div className="flex items-center gap-3 px-4 py-2.5 text-left">
                        <div className="w-8 flex items-center justify-center">
                          {isExpanded ? (
                            <ChevronDown className="w-4 h-4 text-gray-500" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-gray-500" />
                          )}
                        </div>
                        <div className="flex-1 grid grid-cols-5 gap-4 items-center">
                          <div className="text-sm font-mono text-gray-900 dark:text-gray-100 truncate">
                            {session.sessionId.slice(0, 12)}...
                          </div>
                          <div className="text-sm text-gray-600 dark:text-gray-400">
                            {formatRelativeTime(session.startTime)}
                          </div>
                          <div className="flex items-center gap-2">
                            <StatusBadge status={session.status} />
                            <span className="text-sm text-gray-600 dark:text-gray-400">
                              {session.status}
                            </span>
                          </div>
                          <div className="text-sm text-gray-600 dark:text-gray-400">
                            {session.durationMs != null
                              ? `${(session.durationMs / 1000).toFixed(2)}s`
                              : "—"}
                          </div>
                          <div className="text-sm text-gray-600 dark:text-gray-400">
                            {session.toolCount}
                          </div>
                        </div>
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="border-t border-gray-200 dark:border-gray-800">
                        {detailLoading ? (
                          <div className="flex items-center justify-center py-8 text-sm text-gray-500 dark:text-gray-400">
                            {t("common.loading") || "Loading..."}
                          </div>
                        ) : transformed ? (
                          <div className="h-96">
                            <RunDetail
                              detail={transformed.detail}
                              output={transformed.output}
                              loading={false}
                            />
                          </div>
                        ) : (
                          <div className="flex items-center justify-center py-8 text-sm text-red-500 dark:text-red-400">
                            {t("traces.errorLoading") || "Failed to load trace detail"}
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
