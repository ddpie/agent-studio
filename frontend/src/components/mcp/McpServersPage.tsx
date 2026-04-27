import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw, ShieldAlert, Server } from "lucide-react";
import { useMcpStore } from "../../stores/mcp-store";
import { useWorkspaceStore } from "../../stores/workspace-store";
import {
  enableMcp,
  disableMcp,
  upgradeMcp,
  isPlatformAdmin,
  type McpCatalogTarget,
} from "../../lib/api-client";
import McpTargetRow from "./McpTargetRow";
import EnableMcpModal from "./EnableMcpModal";
import DisableMcpModal from "./DisableMcpModal";
import UpgradeMcpModal from "./UpgradeMcpModal";

export default function McpServersPage() {
  const { t } = useTranslation();
  const currentWs = useWorkspaceStore((s) => s.currentWorkspace);

  const {
    catalog,
    loading,
    error,
    polls,
    setWorkspace,
    refreshCatalog,
    startPoll,
    stopAllPolls,
  } = useMcpStore();

  const [isAdmin, setIsAdmin] = useState(false);
  const [openEnable, setOpenEnable] = useState<McpCatalogTarget | null>(null);
  const [openDisable, setOpenDisable] = useState<McpCatalogTarget | null>(null);
  const [openUpgrade, setOpenUpgrade] = useState<McpCatalogTarget | null>(null);

  useEffect(() => {
    isPlatformAdmin().then(setIsAdmin).catch(() => setIsAdmin(false));
  }, []);

  // Wire workspace into store + load catalog on mount/workspace-change
  useEffect(() => {
    const wsId = currentWs?.workspaceId ?? null;
    setWorkspace(wsId);
    if (wsId) {
      refreshCatalog();
    }
    return () => {
      stopAllPolls();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentWs?.workspaceId]);

  const userRole = currentWs?.role || "";
  const canEnableTarget = (target: McpCatalogTarget) => {
    // Sensitivity gate: high needs admin+; low+medium needs editor+.
    const needAdmin = target.sensitivity === "high";
    const rank: Record<string, number> = {
      viewer: 0,
      editor: 1,
      admin: 2,
      owner: 3,
    };
    const mine = rank[userRole] ?? -1;
    return needAdmin ? mine >= 2 : mine >= 1;
  };

  const anyInflight = useMemo(() => {
    if (!catalog) return false;
    return catalog.targets.some(
      (t) =>
        t.enabled &&
        t.runtime &&
        ["CREATING", "UPDATING", "DELETING"].includes(t.runtime.status),
    );
  }, [catalog]);

  const grouped = useMemo(() => {
    if (!catalog) return new Map<string, McpCatalogTarget[]>();
    const m = new Map<string, McpCatalogTarget[]>();
    for (const target of catalog.targets) {
      const cat = target.category || "general";
      if (!m.has(cat)) m.set(cat, []);
      m.get(cat)!.push(target);
    }
    return m;
  }, [catalog]);

  const enabledCount = useMemo(
    () => catalog?.targets.filter((t) => t.enabled).length ?? 0,
    [catalog],
  );

  // ── Actions ──
  async function handleEnable(target: McpCatalogTarget) {
    if (!currentWs) return;
    await enableMcp(currentWs.workspaceId, target.name);
    setOpenEnable(null);
    await refreshCatalog();
    startPoll(target.name);
  }

  async function handleDisable(target: McpCatalogTarget) {
    if (!currentWs) return;
    await disableMcp(currentWs.workspaceId, target.name);
    setOpenDisable(null);
    await refreshCatalog();
  }

  async function handleUpgrade(target: McpCatalogTarget) {
    if (!currentWs) return;
    await upgradeMcp(currentWs.workspaceId, target.name);
    setOpenUpgrade(null);
    await refreshCatalog();
    startPoll(target.name);
  }

  // ── Render ──
  if (!currentWs) {
    return (
      <div className="p-6 text-xs text-gray-500 dark:text-gray-400">
        {t("workspace.selectFirst")}
      </div>
    );
  }

  if (loading && !catalog) {
    return (
      <div className="p-6 flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
        <Loader2 className="w-4 h-4 animate-spin" />
        {t("common.loading")}
      </div>
    );
  }

  // Empty state: no workspace role
  if (catalog && !catalog.workspaceRoleExists) {
    return (
      <div className="p-6 max-w-2xl">
        <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <Server className="w-4 h-4" />
          {t("mcp.page.title")}
        </h1>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          {t("mcp.page.subtitle")}
        </p>
        <div className="mt-6 rounded-md border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 p-4">
          <div className="flex items-start gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <div>
              <h3 className="text-xs font-semibold text-amber-900 dark:text-amber-200">
                {t("mcp.empty.noRole.title")}
              </h3>
              <p className="text-xs text-amber-900 dark:text-amber-200 mt-1">
                {t("mcp.empty.noRole.body")}
              </p>
              {isAdmin && (
                <a
                  href="#/admin"
                  className="mt-3 inline-block text-xs px-3 py-1.5 rounded-md bg-amber-600 text-white hover:bg-amber-700"
                >
                  {t("mcp.empty.noRole.goToAdmin")}
                </a>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Server className="w-4 h-4" />
            {t("mcp.page.title")}
          </h1>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            {t("mcp.page.enabledCount", { count: enabledCount })} ·{" "}
            <span title={t("mcp.cost.hint")}>{t("mcp.cost.hintShort")}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={() => refreshCatalog()}
          disabled={loading}
          className="text-xs px-2.5 py-1.5 rounded-md border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 inline-flex items-center gap-1 disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} />
          {t("mcp.catalog.refresh")}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3 mb-4">
          <p className="text-xs text-red-800 dark:text-red-300">
            {t("mcp.error.loadFailed")}: {error}
          </p>
        </div>
      )}

      {catalog && catalog.targets.length === 0 && (
        <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/30 p-6 text-center">
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {t("mcp.empty.noTargets")}
          </p>
        </div>
      )}

      {[...grouped.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([category, targets]) => (
          <section key={category} className="mb-6">
            <h2 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-500 mb-2">
              {t(`mcp.catalog.category.${category}`, category)}
            </h2>
            <div className="flex flex-col gap-2">
              {targets.map((target) => (
                <McpTargetRow
                  key={target.name}
                  target={target}
                  anyInflight={anyInflight}
                  canEnable={canEnableTarget(target)}
                  onEnable={setOpenEnable}
                  onDisable={setOpenDisable}
                  onUpgrade={setOpenUpgrade}
                />
              ))}
            </div>
          </section>
        ))}

      {openEnable && (
        <EnableMcpModal
          target={openEnable}
          disabled={!canEnableTarget(openEnable)}
          onConfirm={() => handleEnable(openEnable)}
          onCancel={() => setOpenEnable(null)}
        />
      )}

      {openDisable && currentWs && (
        <DisableMcpModal
          target={openDisable}
          workspaceId={currentWs.workspaceId}
          onConfirm={() => handleDisable(openDisable)}
          onCancel={() => setOpenDisable(null)}
        />
      )}

      {openUpgrade && (
        <UpgradeMcpModal
          target={openUpgrade}
          onConfirm={() => handleUpgrade(openUpgrade)}
          onCancel={() => setOpenUpgrade(null)}
        />
      )}

      {/* Poll indicator: show currently polling targets */}
      {Object.keys(polls).length > 0 && (
        <p className="text-[11px] text-gray-500 dark:text-gray-500 mt-4">
          {t("mcp.polling.watching", {
            targets: Object.keys(polls).join(", "),
          })}
        </p>
      )}
    </div>
  );
}
