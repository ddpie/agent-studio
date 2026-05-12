import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { Database, Search, RefreshCw, Loader2, Plus } from "lucide-react";
import { useKBStore } from "../../stores/kb-store";
import { formatDateTime } from "../../lib/date-format";
import KBCreateDialog from "./KBCreateDialog";

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ACTIVE: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400",
    CREATING: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
    FAILED: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
    DELETING: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
  };
  const cls = colors[status] || colors.ACTIVE;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${cls}`}>
      {status}
    </span>
  );
}

export default function KBList() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { items, loading, error, fetchList } = useKBStore();
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const store = useKBStore();

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const refresh = useCallback(() => {
    fetchList();
  }, [fetchList]);

  const filtered = items.filter(
    (kb) =>
      !search ||
      kb.name.toLowerCase().includes(search.toLowerCase()) ||
      (kb.description || "").toLowerCase().includes(search.toLowerCase())
  );

  const handleCreate = async (name: string, description: string): Promise<string> => {
    const result = await store.create(name, description);
    navigate(`/knowledge-bases/${result.kbId}`);
    return result.kbId;
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {t("kb.title")}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400 dark:text-gray-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("kb.filter")}
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500"
            />
          </div>
          <button
            onClick={refresh}
            className="p-1.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            <Plus className="w-3.5 h-3.5" />
            {t("kb.create")}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading && items.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500">
            <Database className="w-10 h-10 mb-3 opacity-30" />
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {search ? t("common.noResults") : t("kb.empty")}
            </p>
            {!search && (
              <>
                <p className="text-xs mt-1">{t("kb.emptyHint")}</p>
                <button
                  onClick={() => setShowCreate(true)}
                  className="mt-4 flex items-center gap-1 px-3 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600"
                >
                  <Plus className="w-3.5 h-3.5" />
                  {t("kb.create")}
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 dark:border-gray-700">
                  <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {t("kb.name")}
                  </th>
                  <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {t("kb.description")}
                  </th>
                  <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {t("kb.status")}
                  </th>
                  <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {t("kb.docCount")}
                  </th>
                  <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {t("common.lastUpdated") || "Updated"}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((kb) => (
                  <tr
                    key={kb.kbId}
                    onClick={() => navigate(`/knowledge-bases/${kb.kbId}`)}
                    className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer transition-colors"
                  >
                    <td className="py-2.5 px-3 text-gray-900 dark:text-gray-100 font-medium">
                      {kb.name}
                    </td>
                    <td className="py-2.5 px-3 text-gray-500 dark:text-gray-400 max-w-xs truncate">
                      {kb.description || "—"}
                    </td>
                    <td className="py-2.5 px-3">
                      <StatusBadge status={kb.status} />
                    </td>
                    <td className="py-2.5 px-3 text-gray-600 dark:text-gray-400">
                      {kb.docCount}
                    </td>
                    <td className="py-2.5 px-3 text-gray-500 dark:text-gray-400 text-xs">
                      {formatDateTime(kb.updatedAt)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mb-4 px-4 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Create dialog */}
      <KBCreateDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={handleCreate}
      />
    </div>
  );
}
