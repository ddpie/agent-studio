import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink, RefreshCw, Search } from "lucide-react";
import {
  fetchAgentLogs,
  type LogEvent,
  type LogLevel,
  type LogSince,
} from "../../lib/api-client";
import { agentConfig } from "../../config";

/** Deep link to raw CloudWatch (power-user escape hatch). */
function cloudWatchLogsUrl(region: string, agentId: string): string {
  const group = `/aws/bedrock-agentcore/runtimes/${agentId}-DEFAULT`;
  const encoded = encodeURIComponent(group).replace(/%2F/g, "$252F");
  return `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#logsV2:log-groups/log-group/${encoded}`;
}

const SINCE_OPTIONS: LogSince[] = ["15m", "1h", "6h", "24h"];
const LEVEL_OPTIONS: LogLevel[] = ["ALL", "ERROR", "WARN", "INFO"];
const FOLLOW_INTERVAL_MS = 5000;

function levelBadgeClass(level: string): string {
  switch (level) {
    case "ERROR":
      return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
    case "WARN":
      return "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300";
    case "INFO":
      return "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300";
    case "DEBUG":
      return "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300";
    default:
      return "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400";
  }
}

function formatTs(ms: number): string {
  if (!ms) return "-";
  try {
    const d = new Date(ms);
    // HH:MM:SS.mmm YYYY-MM-DD — single line, sortable visually
    const pad = (n: number, w = 2) => String(n).padStart(w, "0");
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
    );
  } catch {
    return String(ms);
  }
}

interface LogRowProps {
  event: LogEvent;
}

function LogRow({ event }: LogRowProps) {
  const [expanded, setExpanded] = useState(false);
  const multiline = event.message.includes("\n");
  const long = event.message.length > 300;
  const collapsible = multiline || long;

  return (
    <li
      className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-800 last:border-b-0"
      data-testid="log-row"
    >
      <div className="flex items-start gap-2 text-xs">
        <span
          className="shrink-0 text-gray-500 dark:text-gray-400 font-mono tabular-nums"
          title={String(event.timestamp)}
        >
          {formatTs(event.timestamp)}
        </span>
        <span
          className={`shrink-0 inline-flex items-center justify-center min-w-[44px] px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${levelBadgeClass(event.level)}`}
        >
          {event.level || "log"}
        </span>
        <pre
          className={`flex-1 whitespace-pre-wrap break-words font-mono text-[11.5px] text-gray-800 dark:text-gray-200 ${!expanded && collapsible ? "max-h-12 overflow-hidden" : ""}`}
        >
          {event.message}
        </pre>
        {collapsible && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="shrink-0 text-[11px] text-blue-600 dark:text-blue-400 hover:underline"
          >
            {expanded ? "−" : "+"}
          </button>
        )}
      </div>
    </li>
  );
}

export interface LogsTabProps {
  agentId: string;
}

export default function LogsTab({ agentId }: LogsTabProps) {
  const { t } = useTranslation();

  const [since, setSince] = useState<LogSince>("1h");
  const [level, setLevel] = useState<LogLevel>("ALL");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [follow, setFollow] = useState(false);

  // Debounce search input → actual search term (500ms).
  useEffect(() => {
    const handle = window.setTimeout(() => setSearch(searchInput.trim()), 500);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  const load = useCallback(
    async (opts: { append?: boolean; cursor?: string } = {}) => {
      setLoading(true);
      try {
        const page = await fetchAgentLogs(agentId, {
          since,
          level,
          search: search || undefined,
          cursor: opts.cursor,
        });
        if (!mountedRef.current) return;
        setError(null);
        setEvents((prev) => (opts.append ? [...prev, ...page.events] : page.events));
        setCursor(page.nextCursor);
      } catch (err) {
        if (!mountedRef.current) return;
        setError(err as Error);
      } finally {
        if (mountedRef.current) setLoading(false);
      }
    },
    [agentId, since, level, search]
  );

  // Initial + filter-change load.
  useEffect(() => {
    void load({ append: false });
  }, [load]);

  // Follow mode: auto-refresh when enabled. Only supported for ≤ 1h
  // windows to avoid thrashing the CloudWatch API.
  const followEligible = since === "15m" || since === "1h";
  useEffect(() => {
    if (!follow || !followEligible) return;
    const handle = window.setInterval(() => {
      void load({ append: false });
    }, FOLLOW_INTERVAL_MS);
    return () => window.clearInterval(handle);
  }, [follow, followEligible, load]);

  const logsUrl = useMemo(
    () => cloudWatchLogsUrl(agentConfig.region, agentId),
    [agentId]
  );

  return (
    <div className="p-4" data-testid="logs-tab">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select
          value={since}
          onChange={(e) => setSince(e.target.value as LogSince)}
          className="text-xs border rounded px-2 py-1 bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-gray-900 dark:text-gray-100"
          aria-label={t("logs.since")}
          data-testid="logs-since-select"
        >
          {SINCE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`logs.sinceOpts.${opt}`)}
            </option>
          ))}
        </select>
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value as LogLevel)}
          className="text-xs border rounded px-2 py-1 bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-gray-900 dark:text-gray-100"
          aria-label={t("logs.level")}
          data-testid="logs-level-select"
        >
          {LEVEL_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {t(`logs.levelOpts.${opt}`)}
            </option>
          ))}
        </select>
        <div className="relative">
          <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder={t("logs.searchPlaceholder")}
            className="text-xs border rounded pl-6 pr-2 py-1 bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 w-48 text-gray-900 dark:text-gray-100"
            data-testid="logs-search-input"
          />
        </div>
        <button
          type="button"
          onClick={() => void load({ append: false })}
          disabled={loading}
          className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
          data-testid="logs-refresh-btn"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("common.refresh")}
        </button>
        <label
          className={`inline-flex items-center gap-1 text-xs ${followEligible ? "text-gray-700 dark:text-gray-300" : "text-gray-400 dark:text-gray-500 cursor-not-allowed"}`}
          title={!followEligible ? t("logs.followRangeHint") : undefined}
        >
          <input
            type="checkbox"
            checked={follow && followEligible}
            disabled={!followEligible}
            onChange={(e) => setFollow(e.target.checked)}
            data-testid="logs-follow-toggle"
          />
          {t("logs.follow")}
        </label>
        <a
          href={logsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1 text-xs text-blue-600 dark:text-blue-400 hover:underline"
        >
          <ExternalLink className="w-3 h-3" />
          {t("logs.openInCloudWatch")}
        </a>
      </div>

      {error && (
        <div className="text-xs text-red-600 dark:text-red-400 mb-2" data-testid="logs-error">
          {t("logs.loadError")}: {error.message}
        </div>
      )}

      {!error && events.length === 0 && !loading && (
        <div className="text-xs text-gray-500 dark:text-gray-400 py-6 text-center" data-testid="logs-empty">
          {t("logs.empty")}
        </div>
      )}

      {events.length > 0 && (
        <ul
          className="border border-gray-200 dark:border-gray-800 rounded bg-white dark:bg-gray-900/40 max-h-[480px] overflow-y-auto"
          data-testid="logs-list"
        >
          {events.map((ev, i) => (
            <LogRow key={`${ev.timestamp}-${i}`} event={ev} />
          ))}
        </ul>
      )}

      {cursor && (
        <div className="mt-2 flex justify-center">
          <button
            type="button"
            onClick={() => void load({ append: true, cursor })}
            disabled={loading}
            className="text-xs px-3 py-1 border border-gray-300 dark:border-gray-700 rounded hover:bg-gray-50 dark:hover:bg-gray-800"
            data-testid="logs-load-more"
          >
            {loading ? t("logs.loading") : t("logs.loadMore")}
          </button>
        </div>
      )}
    </div>
  );
}
