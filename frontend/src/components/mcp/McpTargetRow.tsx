import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Power,
  Upload,
  XCircle,
} from "lucide-react";
import type { McpCatalogTarget } from "../../lib/api-client";

export interface McpTargetRowProps {
  target: McpCatalogTarget;
  /** Any target in this workspace is in-flight (CREATING/UPDATING/DELETING). */
  anyInflight: boolean;
  /** Callbacks that open the corresponding modal. */
  onEnable: (target: McpCatalogTarget) => void;
  onDisable: (target: McpCatalogTarget) => void;
  onUpgrade: (target: McpCatalogTarget) => void;
  /** True when user can enable this target (role/sensitivity permitting). */
  canEnable: boolean;
}

function fmtElapsed(startIso: string): string {
  const started = Date.parse(startIso);
  if (isNaN(started)) return "";
  const s = Math.max(0, Math.floor((Date.now() - started) / 1000));
  const m = Math.floor(s / 60);
  const ss = String(s % 60).padStart(2, "0");
  return `${m}:${ss}`;
}

export default function McpTargetRow({
  target,
  anyInflight,
  onEnable,
  onDisable,
  onUpgrade,
  canEnable,
}: McpTargetRowProps) {
  const { t } = useTranslation();
  const rt = target.runtime;
  const status = rt?.status;
  const isInflight =
    status === "CREATING" || status === "UPDATING" || status === "DELETING";

  // Elapsed timer for CREATING/UPDATING
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isInflight) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [isInflight]);

  const elapsedStr =
    isInflight && rt?.updated_at ? fmtElapsed(rt.updated_at) : "";
  const longRunning =
    isInflight && rt?.updated_at
      ? (now - Date.parse(rt.updated_at)) > 10 * 60 * 1_000
      : false;

  const updateAvailable =
    target.enabled &&
    rt?.image_version &&
    target.latestVersion &&
    rt.image_version !== target.latestVersion &&
    target.latestVersion !== "latest";

  // Sensitivity badge
  const sensitivityLabel =
    target.sensitivity === "medium"
      ? t("mcp.sensitivity.medium")
      : target.sensitivity === "high"
        ? t("mcp.sensitivity.high")
        : t("mcp.sensitivity.low");
  const sensitivityClass =
    target.sensitivity === "high"
      ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300 border-red-200 dark:border-red-900"
      : target.sensitivity === "medium"
        ? "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300 border-amber-200 dark:border-amber-900"
        : "bg-gray-50 text-gray-600 dark:bg-gray-800 dark:text-gray-400 border-gray-200 dark:border-gray-700";

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex flex-col gap-2 bg-white dark:bg-gray-900">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
              {target.name}
            </span>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded border ${sensitivityClass}`}
              role="note"
            >
              {sensitivityLabel}
            </span>
            {updateAvailable && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-blue-200 bg-blue-50 text-blue-700 dark:bg-blue-950/30 dark:text-blue-300 dark:border-blue-900">
                {t("mcp.updateAvailable")}
              </span>
            )}
          </div>
          {target.description && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2">
              {target.description}
            </p>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Status pill + action button */}
          {!target.enabled && (
            <button
              type="button"
              disabled={!canEnable || anyInflight}
              onClick={() => onEnable(target)}
              className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              title={
                !canEnable
                  ? t("mcp.enableConfirm.requiresAdmin")
                  : anyInflight
                    ? t("mcp.error.inflight")
                    : ""
              }
            >
              {t("mcp.action.enable")}
            </button>
          )}

          {target.enabled && status === "CREATING" && (
            <StatusPill
              icon={<Loader2 className="w-3 h-3 animate-spin" />}
              label={t("mcp.status.creating")}
              elapsed={elapsedStr}
              longRunning={longRunning}
              t={t}
            />
          )}

          {target.enabled && status === "UPDATING" && (
            <>
              <StatusPill
                icon={<Loader2 className="w-3 h-3 animate-spin" />}
                label={t("mcp.status.updating")}
                elapsed={elapsedStr}
                longRunning={longRunning}
                t={t}
              />
            </>
          )}

          {target.enabled && status === "DELETING" && (
            <StatusPill
              icon={<Loader2 className="w-3 h-3 animate-spin" />}
              label={t("mcp.status.deleting")}
              elapsed={elapsedStr}
              longRunning={longRunning}
              t={t}
            />
          )}

          {target.enabled && (status === "READY" || status === "ACTIVE") && (
            <>
              <StatusPill
                icon={<CheckCircle2 className="w-3 h-3" />}
                label={t("mcp.status.ready")}
                className="text-green-700 dark:text-green-300 bg-green-50 dark:bg-green-950/30 border-green-200 dark:border-green-900"
              />
              {updateAvailable && (
                <button
                  type="button"
                  disabled={anyInflight}
                  onClick={() => onUpgrade(target)}
                  className="text-xs px-2.5 py-1.5 rounded-md border border-blue-300 dark:border-blue-800 text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950/30 disabled:opacity-50 inline-flex items-center gap-1"
                >
                  <Upload className="w-3 h-3" />
                  {t("mcp.action.upgrade")}
                </button>
              )}
              <button
                type="button"
                disabled={anyInflight}
                onClick={() => onDisable(target)}
                className="text-xs px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50 inline-flex items-center gap-1"
                title={anyInflight ? t("mcp.error.inflight") : ""}
              >
                <Power className="w-3 h-3" />
                {t("mcp.action.disable")}
              </button>
            </>
          )}

          {target.enabled && status === "FAILED" && (
            <>
              <StatusPill
                icon={<XCircle className="w-3 h-3" />}
                label={t("mcp.status.failed")}
                className="text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-900"
              />
              <button
                type="button"
                disabled={anyInflight}
                onClick={() => onDisable(target)}
                className="text-xs px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
              >
                {t("mcp.action.disable")}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Secondary row: image version + last_error */}
      {target.enabled && rt && (
        <div className="flex items-center gap-3 text-[11px] text-gray-500 dark:text-gray-500">
          {rt.image_version && (
            <span>
              {t("mcp.image")}: <span className="font-mono">{rt.image_version}</span>
            </span>
          )}
          {rt.updated_at && (
            <span>
              {t("mcp.updatedAt")}: {new Date(rt.updated_at).toLocaleString()}
            </span>
          )}
          {rt.last_error && (
            <span className="text-red-600 dark:text-red-400 inline-flex items-center gap-1">
              <AlertCircle className="w-3 h-3" />
              {rt.last_error}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function StatusPill({
  icon,
  label,
  elapsed,
  longRunning,
  className = "text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700",
  t,
}: {
  icon: React.ReactNode;
  label: string;
  elapsed?: string;
  longRunning?: boolean;
  className?: string;
  t?: (k: string) => string;
}) {
  return (
    <span
      className={`text-xs px-2.5 py-1.5 rounded-md border inline-flex items-center gap-1.5 ${className}`}
    >
      {icon}
      <span>{label}</span>
      {elapsed && <span className="font-mono text-[10px]">{elapsed}</span>}
      {longRunning && t && (
        <span className="text-amber-700 dark:text-amber-300 ml-1">
          · {t("mcp.polling.longRunning")}
        </span>
      )}
    </span>
  );
}
