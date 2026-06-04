import { useTranslation } from "react-i18next";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useRuntimeVersions } from "../../hooks/useRuntimeVersions";
import StatusBadge from "../common/StatusBadge";
import { agentConfig } from "../../config";
import { formatDateTime } from "../../lib/date-format";

function cloudWatchLogsUrl(region: string, agentId: string): string {
  const group = `/aws/bedrock-agentcore/runtimes/${agentId}-DEFAULT`;
  const encoded = encodeURIComponent(group).replace(/%2F/g, "$252F");
  return `https://${region}.console.aws.amazon.com/cloudwatch/home?region=${region}#logsV2:log-groups/log-group/${encoded}`;
}

export interface DeploymentsTabProps {
  agentId: string;
}

export default function DeploymentsTab({ agentId }: DeploymentsTabProps) {
  const { t } = useTranslation();
  const { data, error, loading, refresh } = useRuntimeVersions(agentId);
  const logsUrl = cloudWatchLogsUrl(agentConfig.region, agentId);

  return (
    <div className="p-4" data-testid="deployments-tab">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">{t("deployments.tab")}</h3>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="inline-flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("common.refresh")}
        </button>
      </div>
      {error && (
        <div className="text-sm text-red-600 dark:text-red-400">
          {t("deployments.loadError")}: {error.message}
        </div>
      )}
      {data?.length === 0 && (
        <div className="text-sm text-gray-500 dark:text-gray-400">{t("deployments.empty")}</div>
      )}
      {data && data.length > 0 && (
        <table className="w-full text-sm" data-testid="deployments-table">
          <thead className="text-xs text-gray-500 dark:text-gray-400 uppercase">
            <tr>
              <th className="text-left py-2">{t("deployments.version")}</th>
              <th className="text-left py-2">{t("deployments.status")}</th>
              <th className="text-left py-2">{t("deployments.lastUpdated")}</th>
              <th className="text-right py-2"></th>
            </tr>
          </thead>
          <tbody>
            {data.map((v) => (
              <tr key={v.agentRuntimeVersion} className="border-t dark:border-gray-800">
                <td className="py-2 font-mono">v{v.agentRuntimeVersion}</td>
                <td className="py-2"><StatusBadge status={v.status} /></td>
                <td className="py-2 text-gray-600 dark:text-gray-400">
                  {formatDateTime(v.lastUpdatedAt, "-")}
                </td>
                <td className="py-2 text-right">
                  <a
                    href={logsUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:underline text-xs"
                  >
                    <ExternalLink className="w-3 h-3" />
                    {t("deployments.viewLogs")}
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
