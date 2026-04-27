import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  RefreshCw,
  ShieldAlert,
  Server,
  Upload,
  CheckCircle2,
  XCircle,
  Clock,
} from "lucide-react";
import {
  adminGetMcpFleet,
  adminBroadcastUpgrade,
  isPlatformAdmin,
  type McpFleetRow,
} from "../../lib/api-client";

/**
 * Platform-admin fleet view (spec §8.1 — P0-A fix from round 2).
 *
 * Aggregate table: workspace × target → status/version/last_error.
 * Broadcast-upgrade button per target column header triggers rolling
 * upgrade across all workspaces (batched 10 at a time, 30s between
 * batches; MFA+audit via existing platform-admin plumbing).
 */
export default function AdminMcpFleetPage() {
  const { t } = useTranslation();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);
  const [rows, setRows] = useState<McpFleetRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [broadcasting, setBroadcasting] = useState<string | null>(null);

  useEffect(() => {
    isPlatformAdmin().then(setIsAdmin).catch(() => setIsAdmin(false));
  }, []);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await adminGetMcpFleet();
      setRows(resp.rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isAdmin) load();
  }, [isAdmin]);

  const handleBroadcast = async (target: string) => {
    const matching = rows.filter((r) => r.target === target);
    const affected = matching.length;
    if (!confirm(t("mcp.fleet.broadcastConfirm", { target, count: affected }))) {
      return;
    }
    setBroadcasting(target);
    try {
      const result = await adminBroadcastUpgrade(target);
      alert(
        t("mcp.fleet.broadcastResult", {
          target: result.target,
          upgraded: result.upgraded,
          total: result.total,
        }),
      );
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    } finally {
      setBroadcasting(null);
    }
  };

  const filtered = useMemo(() => {
    if (!filter.trim()) return rows;
    const q = filter.toLowerCase();
    return rows.filter(
      (r) =>
        r.workspaceId.toLowerCase().includes(q) ||
        r.workspaceName.toLowerCase().includes(q) ||
        r.target.toLowerCase().includes(q),
    );
  }, [rows, filter]);

  const byTarget = useMemo(() => {
    const m = new Map<string, McpFleetRow[]>();
    for (const r of filtered) {
      if (!m.has(r.target)) m.set(r.target, []);
      m.get(r.target)!.push(r);
    }
    return m;
  }, [filtered]);

  if (isAdmin === null) {
    return (
      <div className="p-6 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
        <Loader2 className="w-4 h-4 animate-spin" />
        {t("common.loading")}
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="p-6 max-w-2xl">
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-4 flex items-start gap-2">
          <ShieldAlert className="w-4 h-4 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
          <p className="text-xs text-red-800 dark:text-red-300">
            {t("admin.notAuthorized")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Server className="w-4 h-4" />
            {t("mcp.fleet.title")}
          </h1>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            {t("mcp.fleet.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t("mcp.fleet.filterPlaceholder")}
            className="text-xs px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 placeholder:text-gray-400"
          />
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="text-xs px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 inline-flex items-center gap-1 disabled:opacity-50"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
            {t("mcp.catalog.refresh")}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3 mb-4">
          <p className="text-xs text-red-800 dark:text-red-300">{error}</p>
        </div>
      )}

      {loading && rows.length === 0 ? (
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 py-10 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t("common.loading")}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-xs text-gray-500 dark:text-gray-400 py-10 text-center">
          {t("mcp.fleet.empty")}
        </p>
      ) : (
        <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
          {[...byTarget.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([target, trows]) => (
              <TargetSection
                key={target}
                target={target}
                rows={trows}
                onBroadcast={() => handleBroadcast(target)}
                broadcasting={broadcasting === target}
                disableBroadcast={broadcasting !== null}
                t={t}
              />
            ))}
        </div>
      )}
    </div>
  );
}

function TargetSection({
  target,
  rows,
  onBroadcast,
  broadcasting,
  disableBroadcast,
  t,
}: {
  target: string;
  rows: McpFleetRow[];
  onBroadcast: () => void;
  broadcasting: boolean;
  disableBroadcast: boolean;
  t: (k: string, vars?: Record<string, unknown>) => string;
}) {
  return (
    <section className="border-b border-gray-200 dark:border-gray-700 last:border-b-0">
      <header className="bg-gray-50 dark:bg-gray-900/50 px-4 py-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-900 dark:text-gray-100">
          {target} <span className="text-gray-500 dark:text-gray-500 font-normal">· {rows.length}</span>
        </h3>
        <button
          type="button"
          onClick={onBroadcast}
          disabled={disableBroadcast}
          className="text-[11px] px-2 py-1 rounded-md border border-blue-300 dark:border-blue-800 text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950/30 disabled:opacity-50 inline-flex items-center gap-1"
        >
          {broadcasting ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Upload className="w-3 h-3" />
          )}
          {t("mcp.fleet.broadcastUpgrade")}
        </button>
      </header>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500 dark:text-gray-500 border-b border-gray-200 dark:border-gray-700">
            <th className="px-4 py-1.5 font-medium">{t("mcp.fleet.colWorkspace")}</th>
            <th className="px-4 py-1.5 font-medium">{t("mcp.fleet.colStatus")}</th>
            <th className="px-4 py-1.5 font-medium">{t("mcp.fleet.colVersion")}</th>
            <th className="px-4 py-1.5 font-medium">{t("mcp.fleet.colUpdated")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={`${r.workspaceId}-${r.target}-${i}`}
              className="border-b border-gray-100 dark:border-gray-800 last:border-b-0"
            >
              <td className="px-4 py-2 text-gray-700 dark:text-gray-300">
                <div>{r.workspaceName}</div>
                <div className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">
                  {r.workspaceId}
                </div>
              </td>
              <td className="px-4 py-2">
                <StatusBadge status={r.status} lastError={r.lastError} />
              </td>
              <td className="px-4 py-2 font-mono text-gray-600 dark:text-gray-400">
                {r.imageVersion || "—"}
              </td>
              <td className="px-4 py-2 text-gray-500 dark:text-gray-500">
                {r.updatedAt ? new Date(r.updatedAt).toLocaleString() : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function StatusBadge({
  status,
  lastError,
}: {
  status: string;
  lastError?: string | null;
}) {
  if (status === "READY" || status === "ACTIVE") {
    return (
      <span className="inline-flex items-center gap-1 text-green-700 dark:text-green-400">
        <CheckCircle2 className="w-3 h-3" />
        {status}
      </span>
    );
  }
  if (status === "FAILED") {
    return (
      <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-400" title={lastError || ""}>
        <XCircle className="w-3 h-3" />
        {status}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-gray-600 dark:text-gray-400">
      <Clock className="w-3 h-3" />
      {status}
    </span>
  );
}
