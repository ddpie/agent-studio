import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, X } from "lucide-react";
import {
  getAgentsUsingMcp,
  type AgentUsingMcp,
  type McpCatalogTarget,
} from "../../lib/api-client";

export interface DisableMcpModalProps {
  target: McpCatalogTarget;
  workspaceId: string;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
}

export default function DisableMcpModal({
  target,
  workspaceId,
  onConfirm,
  onCancel,
}: DisableMcpModalProps) {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<AgentUsingMcp[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAgentsUsingMcp(workspaceId, target.name)
      .then((r) => {
        if (!cancelled) setAgents(r.agents);
      })
      .catch((e) => {
        if (!cancelled) setLoadErr(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, target.name]);

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg w-full max-w-md shadow-xl">
        <div className="flex items-start justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {t("mcp.disableConfirm.title")}: {target.name}
          </h2>
          <button
            type="button"
            onClick={onCancel}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-gray-700 dark:text-gray-300">
            {t("mcp.disableConfirm.body")}
          </p>

          {agents === null && !loadErr && (
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
              <Loader2 className="w-3 h-3 animate-spin" />
              {t("common.loading")}
            </div>
          )}

          {agents && agents.length > 0 && (
            <div className="rounded-md border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 p-3">
              <div className="flex items-start gap-2 mb-2">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
                <p className="text-xs text-amber-900 dark:text-amber-200">
                  {t("mcp.disableConfirm.affectedAgents", {
                    count: agents.length,
                  })}
                </p>
              </div>
              <ul className="list-disc pl-5 text-xs text-amber-900 dark:text-amber-200 space-y-0.5">
                {agents.map((a) => (
                  <li key={a.agentId}>
                    {a.name}
                    {a.lastInvokedAt && (
                      <span className="ml-1 text-[11px] opacity-75">
                        ({new Date(a.lastInvokedAt).toLocaleDateString()})
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-amber-800 dark:text-amber-300 mt-2">
                {t("mcp.disableConfirm.redeployHint")}
              </p>
            </div>
          )}

          {agents && agents.length === 0 && (
            <p className="text-xs text-green-700 dark:text-green-400">
              {t("mcp.disableConfirm.noAffectedAgents")}
            </p>
          )}

          {loadErr && (
            <p className="text-xs text-red-600 dark:text-red-400">{loadErr}</p>
          )}

          {error && (
            <p className="text-xs text-red-600 dark:text-red-400 whitespace-pre-wrap">
              {error}
            </p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="text-xs px-3 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={submitting}
            className="text-xs px-3 py-1.5 rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 inline-flex items-center gap-1"
          >
            {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
            {t("mcp.action.disable")}
          </button>
        </div>
      </div>
    </div>
  );
}
