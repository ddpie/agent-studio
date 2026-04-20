import { useTranslation } from "react-i18next";
import { X, RefreshCw, ExternalLink } from "lucide-react";
import { useRuntimeStatus } from "../../hooks/useRuntimeStatus";
import StatusBadge from "../common/StatusBadge";
import { agentConfig } from "../../config";
import { formatDateTime } from "../../lib/date-format";

export interface AgentRuntimeDrawerProps {
  agentId: string | null;
  onClose: () => void;
}

function cloudWatchLogsUrl(region: string, agentId: string): string {
  const group = `/aws/bedrock-agentcore/runtimes/${agentId}-DEFAULT`;
  const encoded = encodeURIComponent(group).replace(/%2F/g, "$252F");
  return `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#logsV2:log-groups/log-group/${encoded}`;
}

export default function AgentRuntimeDrawer({ agentId, onClose }: AgentRuntimeDrawerProps) {
  const { t } = useTranslation();
  const { data, error, loading, refresh } = useRuntimeStatus(agentId);

  if (!agentId) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="runtime-drawer"
      className="fixed inset-y-0 right-0 z-40 w-[380px] max-w-full shadow-2xl bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-800 flex flex-col"
    >
      <div className="flex items-center justify-between p-4 border-b dark:border-gray-800">
        <h3 className="text-sm font-semibold">{t("runtime.drawer.title")}</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("runtime.drawer.close")}
          className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="p-4 flex-1 overflow-y-auto text-sm space-y-3">
        {error && <div className="text-red-600 dark:text-red-400">{error.message}</div>}
        {loading && !data && <div className="text-gray-500 dark:text-gray-400">Loading…</div>}
        {data && (
          <>
            <div className="flex items-center gap-2">
              <StatusBadge status={data.status} />
              <button
                type="button"
                onClick={refresh}
                className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 flex items-center gap-1"
              >
                <RefreshCw className="w-3 h-3" />
                {t("runtime.drawer.refresh")}
              </button>
            </div>
            {data.agentRuntimeVersion && (
              <div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{t("runtime.drawer.version")}</div>
                <div>v{data.agentRuntimeVersion}</div>
              </div>
            )}
            {data.lastUpdatedAt && (
              <div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{t("runtime.drawer.lastUpdated")}</div>
                <div>{formatDateTime(data.lastUpdatedAt)}</div>
              </div>
            )}
            {data.description && (
              <div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{t("runtime.drawer.description")}</div>
                <div className="whitespace-pre-wrap">{data.description}</div>
              </div>
            )}
            <a
              href={cloudWatchLogsUrl(agentConfig.region, agentId)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:underline"
            >
              <ExternalLink className="w-4 h-4" />
              {t("runtime.drawer.viewLogs")}
            </a>
          </>
        )}
      </div>
    </div>
  );
}
