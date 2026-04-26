import { ShieldCheck, Building2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { isPlatformAdmin } from "../../lib/api-client";
import AdminWorkspaceIamPanel from "../admin/AdminWorkspaceIamPanel";

type TabId = "workspaces";

const VALID_TABS: TabId[] = ["workspaces"];

export default function AdminConsolePage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [isAdmin, setIsAdmin] = useState<boolean | null>(null);

  useEffect(() => {
    isPlatformAdmin().then(setIsAdmin);
  }, []);

  const rawTab = searchParams.get("tab") as TabId | null;
  const tab: TabId = rawTab && VALID_TABS.includes(rawTab) ? rawTab : "workspaces";
  const setTab = (id: TabId) => {
    const next = new URLSearchParams(searchParams);
    if (id === "workspaces") next.delete("tab");
    else next.set("tab", id);
    setSearchParams(next, { replace: true });
  };

  // Loading state
  if (isAdmin === null) {
    return (
      <div className="max-w-4xl p-6 overflow-y-auto">
        <div className="flex items-center justify-center py-12">
          <div className="w-4 h-4 border-2 border-gray-300 dark:border-gray-600 border-t-blue-500 rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  // Not admin — show no-access message
  if (!isAdmin) {
    return (
      <div className="max-w-4xl p-6 overflow-y-auto">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-5">
          <ShieldCheck className="w-4 h-4" /> {t("admin.title")}
        </h2>
        <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-4">
          <p className="text-xs text-red-700 dark:text-red-400">
            {t("admin.noAccess")}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full max-w-6xl p-6">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-5 shrink-0">
        <ShieldCheck className="w-4 h-4" /> {t("admin.title")}
      </h2>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4 shrink-0">
        {([
          { id: "workspaces" as const, icon: Building2, label: t("admin.tabWorkspaces") },
        ]).map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs border-b-2 -mb-px transition-colors ${
              tab === id
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            }`}
          >
            <Icon className="w-3.5 h-3.5" /> {label}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0">
        {tab === "workspaces" && <AdminWorkspaceIamPanel />}
      </div>
    </div>
  );
}
