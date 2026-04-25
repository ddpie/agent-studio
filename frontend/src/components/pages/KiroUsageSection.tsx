import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Activity, RefreshCw, ExternalLink, Info } from "lucide-react";
import { getKiroUsage, type KiroUsageInfo } from "../../lib/api-client";

// Localized mapping of runtime error codes to user-friendly messages.
// Centralized so i18n keys are all in one place and new codes are easy
// to add without grepping the component tree.
const ERROR_KEYS: Record<string, string> = {
  kiro_not_configured: "kiroUsage.errorNotConfigured",
  usage_cli_failed: "kiroUsage.errorCliFailed",
  usage_parse_failed: "kiroUsage.errorParseFailed",
  runtime_invoke_failed: "kiroUsage.errorRuntimeFailed",
};

function formatCredits(n: number | undefined): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  // Kiro reports sub-credit precision (e.g. 204.98); round to 2 for
  // display but keep the full value in the progress-bar math.
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function percent(used?: number, cap?: number): number {
  if (!cap || !used) return 0;
  const pct = (used / cap) * 100;
  return Math.min(100, Math.max(0, pct));
}

// All workspace members (viewer+) can see usage so they can alert the
// admin when credits run low. The admin-only surface is key rotation
// itself (KiroKeySection). That split is enforced server-side — the
// frontend role flag here is advisory only for UI hiding.
export default function KiroUsageSection() {
  const { t } = useTranslation();
  const [data, setData] = useState<KiroUsageInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await getKiroUsage());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "load failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Nothing to show if no key configured — the sibling KiroKeySection
  // already surfaces that case with a big amber banner, no need to
  // repeat.
  if (data && !data.configured) return null;

  const used = data?.currentUsage;
  const cap = data?.usageLimit;
  const pct = percent(used, cap);
  const pctBucket = pct >= 90 ? "red" : pct >= 70 ? "amber" : "green";
  const barColor = {
    red: "bg-red-500 dark:bg-red-600",
    amber: "bg-amber-500 dark:bg-amber-600",
    green: "bg-green-500 dark:bg-green-600",
  }[pctBucket];

  const errorKey = data?.error ? ERROR_KEYS[data.error] : null;

  return (
    <div className="rounded-md border border-gray-200 dark:border-gray-800 p-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-gray-500 dark:text-gray-400" />
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {t("kiroUsage.title", "Kiro Credits")}
          </h3>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded border border-gray-200 dark:border-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-900 disabled:opacity-50"
          title={t("kiroUsage.refresh", "Refresh")}
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("kiroUsage.refresh", "Refresh")}
        </button>
      </div>
      <p className="flex items-start gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 mb-3 leading-relaxed">
        <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
        <span>
          {t(
            "kiroUsage.subscriptionScope",
            "Shows the total credits used on this Kiro subscription — including any usage from the Kiro IDE or CLI tied to the same account, not just calls made through Agent Studio.",
          )}
        </span>
      </p>

      {loading && !data && (
        <div className="text-[11px] text-gray-400">{t("common.loading")}</div>
      )}

      {err && (
        <div className="text-[11px] text-red-600 dark:text-red-400">{err}</div>
      )}

      {data?.configured && !data.error && data.currentUsage !== undefined && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <div className="flex items-center gap-2">
              <span className="text-gray-700 dark:text-gray-300 font-medium">
                {formatCredits(used)} / {formatCredits(cap)} {t("kiroUsage.credits", "credits")}
              </span>
              {data.tier && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-900/60">
                  {data.tier}
                </span>
              )}
            </div>
            <span className="text-gray-500 dark:text-gray-400">{pct.toFixed(0)}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-gray-100 dark:bg-gray-800 overflow-hidden">
            <div className={`h-full ${barColor} transition-all`} style={{ width: `${pct}%` }} />
          </div>
          <div className="flex items-center justify-between text-[10px] text-gray-500 dark:text-gray-400">
            <span>
              {data.resetsOn
                ? t("kiroUsage.resetsOn", "Resets on {{date}}", { date: data.resetsOn })
                : ""}
            </span>
            <a
              href="https://app.kiro.dev/account/usage"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-0.5 text-blue-600 dark:text-blue-400 hover:underline"
            >
              {t("kiroUsage.openDashboard", "Manage plan")}
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
          {data.overagesEnabled && data.overageUsed && data.overageUsed > 0 ? (
            <div className="text-[10px] text-amber-700 dark:text-amber-300">
              {t("kiroUsage.overage", "Overage this period: {{amount}} {{currency}} ({{rate}}/req)", {
                amount: ((data.overageUsed || 0) * (data.overageRate || 0)).toFixed(2),
                currency: data.currency || "USD",
                rate: `${data.currency || "$"}${(data.overageRate || 0).toFixed(2)}`,
              })}
            </div>
          ) : null}
          {data.fetchedAt && (
            <div className="text-[10px] text-gray-400 dark:text-gray-500">
              {t("kiroUsage.fetchedAt", "Fetched {{time}}", {
                time: new Date(data.fetchedAt).toLocaleTimeString(),
              })}
            </div>
          )}
        </div>
      )}

      {data?.configured && data.error && (
        <div className="text-[11px] text-amber-700 dark:text-amber-300">
          {errorKey
            ? t(errorKey, "Failed to fetch Kiro usage.")
            : t("kiroUsage.errorGeneric", "Failed to fetch Kiro usage.")}
        </div>
      )}
    </div>
  );
}
