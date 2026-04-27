import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ExternalLink, Loader2, Shield, ShieldCheck, AlertCircle } from "lucide-react";
import {
  createWorkspaceRole,
  getWorkspacePermissions,
} from "../../lib/api-client";

export interface AdminWorkspaceRoleCardProps {
  workspaceId: string;
  workspaceName?: string;
  onRoleCreated?: (wsId: string) => void;
}

/**
 * Slimmed-down workspace role admin view (spec §8.1 — post v4 refactor).
 *
 * Shows role ARN + boundary binding; create/recreate workspace role
 * button; link to /#/mcp for actual grant management. Replaces the old
 * per-target grant UI in IamPermissionsTab.
 */
export default function AdminWorkspaceRoleCard({
  workspaceId,
  workspaceName,
  onRoleCreated,
}: AdminWorkspaceRoleCardProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [hasRole, setHasRole] = useState<boolean | null>(null);
  const [roleArn, setRoleArn] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getWorkspacePermissions(workspaceId, []);
      setHasRole(resp.hasRole);
      setRoleArn(resp.roleArn ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      await createWorkspaceRole(workspaceId);
      if (onRoleCreated) onRoleCreated(workspaceId);
      await loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="p-4 max-w-2xl">
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <Shield className="w-4 h-4" />
          {workspaceName || t("workspace.unnamed")}
        </h3>
        <p className="text-[10px] text-gray-400 dark:text-gray-500 font-mono mt-0.5">
          {workspaceId}
        </p>
      </div>

      {loading && !hasRole && !error && (
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t("common.loading")}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3 mb-3">
          <p className="text-xs text-red-800 dark:text-red-300 flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            {error}
          </p>
        </div>
      )}

      {/* ── Role status card ── */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 mb-4">
        <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
          {t("admin.role.title")}
        </h4>

        {hasRole ? (
          <div>
            <div className="flex items-center gap-2 text-xs text-green-700 dark:text-green-400">
              <ShieldCheck className="w-3.5 h-3.5" />
              {t("admin.role.exists")}
            </div>
            {roleArn && (
              <p className="text-[10px] text-gray-500 dark:text-gray-500 font-mono break-all mt-1">
                {roleArn}
              </p>
            )}
            <p className="text-[11px] text-gray-500 dark:text-gray-500 mt-2">
              {t("admin.role.boundaryNote")}
            </p>
          </div>
        ) : hasRole === false ? (
          <div>
            <p className="text-xs text-gray-600 dark:text-gray-400 mb-3">
              {t("admin.role.notCreatedDesc")}
            </p>
            <button
              type="button"
              onClick={handleCreate}
              disabled={creating}
              className="text-xs px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-1"
            >
              {creating && <Loader2 className="w-3 h-3 animate-spin" />}
              {t("admin.role.createBtn")}
            </button>
          </div>
        ) : null}
      </section>

      {/* ── MCP management link ── */}
      {hasRole && (
        <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 mb-2">
            {t("admin.role.manageMcps")}
          </h4>
          <p className="text-xs text-gray-600 dark:text-gray-400 mb-3">
            {t("admin.role.manageMcpsHint")}
          </p>
          <a
            href="#/mcp"
            className="text-xs px-3 py-1.5 rounded-md border border-blue-300 dark:border-blue-800 text-blue-700 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-950/30 inline-flex items-center gap-1"
          >
            <ExternalLink className="w-3 h-3" />
            {t("admin.role.goToMcps")}
          </a>
        </section>
      )}
    </div>
  );
}
