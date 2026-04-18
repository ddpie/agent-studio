import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { useRuntimeEndpoints } from "../../hooks/useRuntimeEndpoints";
import { useRuntimeVersions } from "../../hooks/useRuntimeVersions";
import {
  createAgentEndpoint,
  updateAgentEndpoint,
  deleteAgentEndpoint,
  type AgentEndpoint,
} from "../../lib/api-client";
import StatusBadge from "../common/StatusBadge";
import EndpointDialog from "./EndpointDialog";
import { toast } from "../../lib/toast";

export interface EndpointsTabProps {
  agentId: string;
}

export default function EndpointsTab({ agentId }: EndpointsTabProps) {
  const { t } = useTranslation();
  const endpoints = useRuntimeEndpoints(agentId);
  const versions = useRuntimeVersions(agentId);
  const [dialog, setDialog] = useState<null | { mode: "create" | "switch"; fixedName?: string }>(null);

  const versionStrings = useMemo(
    () => (versions.data ?? []).map((v) => v.agentRuntimeVersion),
    [versions.data]
  );

  async function doCreate({ name, version }: { name: string; version: string }) {
    try {
      await createAgentEndpoint(agentId, { name, version });
      toast.success(t("endpoints.created", { name }));
      setDialog(null);
      endpoints.refresh();
    } catch (err) {
      toast.error(err as Error);
      throw err;
    }
  }

  async function doSwitch(endpointName: string, version: string) {
    try {
      await updateAgentEndpoint(agentId, endpointName, version);
      toast.success(t("endpoints.switched", { name: endpointName, version }));
      setDialog(null);
      endpoints.refresh();
    } catch (err) {
      toast.error(err as Error);
      throw err;
    }
  }

  async function doDelete(ep: AgentEndpoint) {
    if (!confirm(t("endpoints.confirmDelete", { name: ep.name }))) return;
    try {
      await deleteAgentEndpoint(agentId, ep.name);
      toast.success(t("endpoints.deleted", { name: ep.name }));
      endpoints.refresh();
    } catch (err) {
      toast.error(err as Error);
    }
  }

  return (
    <div className="p-4" data-testid="endpoints-tab">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">{t("endpoints.tab")}</h3>
        <button
          type="button"
          onClick={() => setDialog({ mode: "create" })}
          disabled={versionStrings.length === 0}
          className="rounded bg-blue-600 text-white px-3 py-1 text-xs font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {t("endpoints.create")}
        </button>
      </div>
      {endpoints.error && <div className="text-red-600 text-sm">{endpoints.error.message}</div>}
      {endpoints.data && (
        <table className="w-full text-sm" data-testid="endpoints-table">
          <thead className="text-xs text-gray-500 uppercase">
            <tr>
              <th className="text-left py-2">Name</th>
              <th className="text-left py-2">Status</th>
              <th className="text-left py-2">{t("endpoints.liveVersion")}</th>
              <th className="text-left py-2">{t("endpoints.targetVersion")}</th>
              <th className="text-right py-2"></th>
            </tr>
          </thead>
          <tbody>
            {endpoints.data.map((ep) => (
              <tr key={ep.name} className="border-t dark:border-gray-800" data-testid={`endpoint-row-${ep.name}`}>
                <td className="py-2 font-mono">{ep.name}</td>
                <td className="py-2"><StatusBadge status={ep.status} /></td>
                <td className="py-2">{ep.liveVersion ? `v${ep.liveVersion}` : "-"}</td>
                <td className="py-2">{ep.targetVersion ? `v${ep.targetVersion}` : "-"}</td>
                <td className="py-2 text-right space-x-2">
                  {ep.name.toUpperCase() !== "DEFAULT" && (
                    <>
                      <button
                        type="button"
                        onClick={() => setDialog({ mode: "switch", fixedName: ep.name })}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        {t("endpoints.switch")}
                      </button>
                      <button
                        type="button"
                        onClick={() => doDelete(ep)}
                        className="text-xs text-red-600 hover:underline inline-flex items-center gap-0.5"
                      >
                        <Trash2 className="w-3 h-3" />
                        {t("endpoints.delete")}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {dialog && (
        <EndpointDialog
          mode={dialog.mode}
          fixedName={dialog.fixedName}
          versions={versionStrings}
          onCancel={() => setDialog(null)}
          onSubmit={(p) =>
            dialog.mode === "create"
              ? doCreate(p)
              : doSwitch(dialog.fixedName!, p.version)
          }
        />
      )}
    </div>
  );
}
