import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Download, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useSessionTrace } from "../../hooks/useTraces";
import { generateDownloadUrl } from "../../lib/s3-storage";
import { formatDateTime } from "../../lib/date-format";
import SpanTree from "./SpanTree";
import type { RunDetail as RunDetailType, RunOutput } from "../../lib/runs-client";

interface Props {
  agentId: string;
  detail: RunDetailType;
  output: RunOutput | null;
  loading: boolean;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded px-2.5 py-1.5">
      <div className="text-[10px] text-gray-500 dark:text-gray-400 uppercase tracking-wide">{label}</div>
      <div className="text-xs text-gray-800 dark:text-gray-200 mt-0.5 truncate font-mono" title={value}>{value}</div>
    </div>
  );
}

function stripS3Markers(text: string): string {
  return text.replace(/__S3_DOWNLOAD__:[^:]+:[^\s"}\]]+/g, "").trim();
}

export default function RunDetail({ agentId, detail, output, loading }: Props) {
  const { t } = useTranslation();
  const [showSpans, setShowSpans] = useState(false);
  const { root: traceRoot, loading: traceLoading, pending: tracePending } = useSessionTrace(
    showSpans ? agentId : null,
    showSpans && detail.sessionId ? detail.sessionId : null
  );

  const handleDownload = async (key: string, filename: string) => {
    try {
      const blobUrl = await generateDownloadUrl(key);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      console.error("Download failed:", err);
    }
  };


  const promptTokens = detail.usage?.promptTokens ?? null;
  const completionTokens = detail.usage?.completionTokens ?? null;
  const totalTokens = detail.usage?.totalTokens ?? null;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {/* Metrics strip */}
        <div className="grid grid-cols-4 gap-3">
          <Metric label={t("runs.model") || "Model"} value={detail.model || "—"} />
          <Metric
            label={t("runs.tokens") || "Tokens"}
            value={
              promptTokens != null && completionTokens != null
                ? `${promptTokens.toLocaleString()} → ${completionTokens.toLocaleString()}`
                : totalTokens != null
                  ? totalTokens.toLocaleString()
                  : "—"
            }
          />
          <Metric
            label={t("runs.latency") || "Latency"}
            value={detail.durationMs != null ? `${(detail.durationMs / 1000).toFixed(2)}s` : "—"}
          />
          <Metric label={t("runs.status") || "Status"} value={t(`runs.statusLabel.${detail.status}`, detail.status)} />
        </div>

        {/* Input */}
        {detail.input && (
          <div>
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">{t("runs.input") || "Input"}</h3>
            <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded p-3 text-sm text-gray-800 dark:text-gray-200 whitespace-pre-wrap">
              {detail.input}
            </div>
          </div>
        )}

        {/* Error */}
        {detail.error && (
          <div>
            <h3 className="text-sm font-semibold text-red-700 dark:text-red-300 mb-2">{t("runs.error") || "Error"}</h3>
            <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded p-3 text-sm text-red-800 dark:text-red-200 whitespace-pre-wrap">
              <div className="font-medium">{detail.error.code}</div>
              <div className="mt-1">{detail.error.message}</div>
            </div>
          </div>
        )}

        {/* Output */}
        {loading && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        )}
        {!loading && output && output.text && (
          <div>
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">{t("runs.output") || "Output"}</h3>
            <div className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded p-4">
              <div className="prose prose-sm max-w-none dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {stripS3Markers(output.text)}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        )}

        {/* Tool Calls */}
        {!loading && output && output.toolCalls && output.toolCalls.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">{t("runs.toolCalls") || "Tool Calls"}</h3>
            <div className="space-y-2">
              {output.toolCalls.map((tool, idx) => {
                const [expanded, setExpanded] = useState(false);
                return (
                  <div key={idx} className="bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded overflow-hidden">
                    <button
                      onClick={() => setExpanded(!expanded)}
                      className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        {expanded ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                        <span className="text-sm font-mono text-gray-800 dark:text-gray-200">{tool.name}</span>
                      </div>
                    </button>
                    {expanded && (
                      <div className="border-t border-gray-200 dark:border-gray-800 p-3 space-y-2">
                        <div>
                          <div className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">{t("runs.toolInput") || "Input"}</div>
                          <pre className="text-xs bg-gray-50 dark:bg-gray-950 p-2 rounded overflow-x-auto text-gray-800 dark:text-gray-200">
                            {typeof tool.input === "string" ? tool.input : JSON.stringify(tool.input, null, 2)}
                          </pre>
                        </div>
                        <div>
                          <div className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1">{t("runs.toolOutput") || "Output"}</div>
                          <pre className="text-xs bg-gray-50 dark:bg-gray-950 p-2 rounded overflow-x-auto text-gray-800 dark:text-gray-200">
                            {JSON.stringify(tool.output, null, 2)}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Attachments */}
        {detail.artifactRefs && detail.artifactRefs.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">{t("runs.attachments") || "Attachments"}</h3>
            <div className="flex flex-wrap gap-2">
              {detail.artifactRefs.map((artifact, idx) => (
                <button
                  key={idx}
                  onClick={() => handleDownload(artifact.key, artifact.filename)}
                  className="flex items-center gap-2 px-3 py-2 rounded border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors text-sm"
                >
                  <Download className="w-4 h-4 text-gray-500 dark:text-gray-400" />
                  <span className="text-gray-800 dark:text-gray-200">{artifact.filename}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Span Tree */}
        {detail.sessionId && (
          <div>
            <button
              onClick={() => setShowSpans(!showSpans)}
              className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2 hover:text-gray-900 dark:hover:text-gray-100"
            >
              {showSpans ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              {t("runs.spanTree") || "Span Tree"}
            </button>
            {showSpans && (
              <>
                {traceLoading && (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
                  </div>
                )}
                {tracePending && !traceLoading && (
                  <div className="text-sm text-gray-500 dark:text-gray-400 italic py-4">
                    {t("runs.spansPending") || "Spans are still being ingested..."}
                  </div>
                )}
                {traceRoot && (
                  <SpanTree root={traceRoot} />
                )}
              </>
            )}
          </div>
        )}

        {/* Timestamp footer */}
        <div className="text-xs text-gray-500 dark:text-gray-400 pt-4 border-t border-gray-200 dark:border-gray-800">
          <div><strong>{t("runs.startedAt") || "Started"}:</strong> {formatDateTime(detail.startedAt)}</div>
          {detail.completedAt && (
            <div className="mt-1"><strong>{t("runs.completedAt") || "Completed"}:</strong> {formatDateTime(detail.completedAt)}</div>
          )}
        </div>
      </div>
    </div>
  );
}
