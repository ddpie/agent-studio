import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Search, Shield, Loader2 } from "lucide-react";
import { useWorkspaceStore, type WorkspaceSummary } from "../../stores/workspace-store";
import { getWorkspacePermissions } from "../../lib/api-client";
import IamPermissionsTab from "../pages/IamPermissionsTab";

/**
 * Admin panel: left sidebar (workspace list) + right panel (IAM details).
 *
 * TODO: Replace workspace source with a dedicated admin endpoint
 * (`GET /api/admin/workspaces`) that returns ALL workspaces regardless
 * of membership. For now we use the workspace store, which only lists
 * workspaces the current user belongs to.
 */
export default function AdminWorkspaceIamPanel() {
  const { t } = useTranslation();
  const { workspaces, loading: wsLoading, loaded, refreshWorkspaces } = useWorkspaceStore();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Per-workspace role status cache: wsId -> boolean (true = has role)
  const [roleStatusCache, setRoleStatusCache] = useState<Record<string, boolean | null>>({});

  // Ensure workspaces are loaded
  useEffect(() => {
    if (!loaded && !wsLoading) {
      refreshWorkspaces();
    }
  }, [loaded, wsLoading, refreshWorkspaces]);

  // Probe each workspace's role status (lightweight: request with empty actions list)
  useEffect(() => {
    if (!loaded) return;
    let cancelled = false;
    for (const ws of workspaces) {
      if (roleStatusCache[ws.workspaceId] !== undefined) continue;
      // Mark as loading (null = pending)
      setRoleStatusCache((prev) => ({ ...prev, [ws.workspaceId]: null }));
      getWorkspacePermissions(ws.workspaceId, [])
        .then((resp) => {
          if (!cancelled) {
            setRoleStatusCache((prev) => ({
              ...prev,
              [ws.workspaceId]: resp.hasRole,
            }));
          }
        })
        .catch(() => {
          if (!cancelled) {
            setRoleStatusCache((prev) => ({
              ...prev,
              [ws.workspaceId]: false,
            }));
          }
        });
    }
    return () => {
      cancelled = true;
    };
    // Only re-run when workspace list changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, workspaces.length]);

  // Filter workspaces by search text
  const filtered = useMemo(() => {
    if (!search.trim()) return workspaces;
    const q = search.toLowerCase();
    return workspaces.filter(
      (ws) =>
        (ws.name ?? "").toLowerCase().includes(q) ||
        ws.workspaceId.toLowerCase().includes(q),
    );
  }, [workspaces, search]);

  const selected = workspaces.find((ws) => ws.workspaceId === selectedId) ?? null;

  // Callback for child to signal role was created — update our cache
  const handleRoleCreated = (wsId: string) => {
    setRoleStatusCache((prev) => ({ ...prev, [wsId]: true }));
  };

  return (
    <div className="flex border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden" style={{ minHeight: 480 }}>
      {/* ── Left panel: workspace list ── */}
      <div className="w-[280px] shrink-0 border-r border-gray-200 dark:border-gray-700 flex flex-col bg-white dark:bg-gray-900">
        {/* Search */}
        <div className="p-2 border-b border-gray-100 dark:border-gray-800">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("admin.workspaceSearch")}
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1.5 px-0.5">
            {t("admin.workspaceCount", { count: filtered.length })}
          </p>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {wsLoading && !loaded ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-6">
              {t("common.noResults")}
            </p>
          ) : (
            filtered.map((ws) => (
              <WorkspaceListItem
                key={ws.workspaceId}
                ws={ws}
                active={ws.workspaceId === selectedId}
                hasRole={roleStatusCache[ws.workspaceId]}
                onClick={() => setSelectedId(ws.workspaceId)}
              />
            ))
          )}
        </div>
      </div>

      {/* ── Right panel: IAM details ── */}
      <div className="flex-1 overflow-y-auto bg-gray-50 dark:bg-gray-950/30">
        {!selected ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 dark:text-gray-500">
            <Shield className="w-8 h-8 opacity-30" />
            <p className="text-xs">{t("admin.selectWorkspace")}</p>
          </div>
        ) : (
          <div className="p-4">
            {/* Workspace header */}
            <div className="mb-4">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {selected.name || t("workspace.unnamed")}
              </h3>
              <p className="text-[10px] text-gray-400 dark:text-gray-500 font-mono mt-0.5">
                {selected.workspaceId}
              </p>
            </div>
            {/* Reuse IamPermissionsTab with explicit workspaceId */}
            <IamPermissionsTab
              key={selected.workspaceId}
              workspaceId={selected.workspaceId}
              readOnly={false}
              onRoleCreated={() => handleRoleCreated(selected.workspaceId)}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Workspace list item ──

function WorkspaceListItem({
  ws,
  active,
  hasRole,
  onClick,
}: {
  ws: WorkspaceSummary;
  active: boolean;
  hasRole: boolean | null | undefined;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const truncatedId =
    ws.workspaceId.length > 12
      ? ws.workspaceId.slice(0, 6) + "..." + ws.workspaceId.slice(-4)
      : ws.workspaceId;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-gray-50 dark:border-gray-800 transition-colors ${
        active
          ? "bg-blue-50 dark:bg-blue-900/20 border-l-2 border-l-blue-500"
          : "hover:bg-gray-50 dark:hover:bg-gray-800/50 border-l-2 border-l-transparent"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={`text-xs font-medium truncate ${
            active
              ? "text-blue-700 dark:text-blue-300"
              : "text-gray-800 dark:text-gray-200"
          }`}
        >
          {ws.name || t("workspace.unnamed")}
        </span>
        {/* Role status dot */}
        {hasRole === null || hasRole === undefined ? (
          <span className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 shrink-0" title={t("common.loading")} />
        ) : hasRole ? (
          <span className="w-2 h-2 rounded-full bg-green-500 shrink-0" title={t("admin.hasRole")} />
        ) : (
          <span className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 shrink-0" title={t("admin.noRole")} />
        )}
      </div>
      <div className="flex items-center gap-2 mt-0.5">
        <span className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">
          {truncatedId}
        </span>
        <span className="text-[10px] text-gray-400 dark:text-gray-500">
          {hasRole ? t("admin.hasRole") : hasRole === false ? t("admin.noRole") : ""}
        </span>
      </div>
    </button>
  );
}
