import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Wrench, Search, RefreshCw, Loader2, Plus, Code2, Lock, Cloud, BarChart3, Globe,
} from "lucide-react";
import { useToolLibraryStore, type ToolTemplate } from "../../stores/tool-library-store";

const CATEGORY_ICONS: Record<string, typeof Code2> = {
  aws: Cloud,
  data: BarChart3,
  web: Globe,
  custom: Wrench,
};

function ToolCard({ tool, onClick }: { tool: ToolTemplate; onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <div
      onClick={onClick}
      className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:border-blue-300 hover:shadow-sm cursor-pointer transition-all text-left"
    >
      <div className="flex items-center gap-2">
        <Code2 className="w-4 h-4 text-blue-500 flex-shrink-0" />
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 flex-1 truncate">
          {tool.name}
        </h3>
        {tool.builtin && (
          <span className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
            <Lock className="w-2.5 h-2.5" />
            {t("tools.builtin")}
          </span>
        )}
      </div>
      {tool.description && (
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-2 line-clamp-2">{tool.description}</p>
      )}
      <div className="flex items-center gap-2 mt-2">
        {(() => { const CatIcon = CATEGORY_ICONS[tool.category] || Wrench; return <CatIcon className="w-3 h-3 text-gray-400" />; })()}
        <span className="text-[10px] text-gray-400">{tool.category}</span>
        <span className="text-[10px] text-gray-300 dark:text-gray-600">·</span>
        <span className="text-[10px] text-gray-400 font-mono">{tool.id}</span>
      </div>
    </div>
  );
}

export default function ToolLibraryPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { tools, loading, fetchTools, error, clearError } = useToolLibraryStore();
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const [builtinCollapsed, setBuiltinCollapsed] = useState(true);

  useEffect(() => { fetchTools(); }, [fetchTools]);

  const refresh = useCallback(() => { fetchTools(); }, [fetchTools]);

  const myTools = tools.filter((t) => !t.builtin);
  const builtinTools = tools.filter((t) => t.builtin);

  const filterFn = (t: ToolTemplate) =>
    !search ||
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    t.description.toLowerCase().includes(search.toLowerCase()) ||
    t.id.toLowerCase().includes(search.toLowerCase());

  const filteredMy = myTools.filter(filterFn);
  const filteredBuiltin = builtinTools.filter(filterFn);

  const handleCreate = async () => {
    if (!createName.trim()) return;
    setCreating(true);
    const id = createName.trim().replace(/[^a-zA-Z0-9_]/g, "_").toLowerCase();
    // Navigate to detail page with "new" flag — ToolDetail handles the rest
    setCreating(false);
    setShowCreate(false);
    setCreateName("");
    setCreateDesc("");
    navigate(`/tools/${id}?new=1&name=${encodeURIComponent(createName.trim())}&desc=${encodeURIComponent(createDesc.trim())}`);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">{t("tools.title")}</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("tools.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("tools.searchPlaceholder")}
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder:text-gray-400"
            />
          </div>
          <button
            onClick={refresh}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            <Plus className="w-3.5 h-3.5" />
            {t("common.create")}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : (
          <div className="space-y-6">
            {/* My Tools */}
            <div>
              <h3 className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3">
                {t("tools.myTools")}
              </h3>
              {filteredMy.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                  <Wrench className="w-10 h-10 mb-3 opacity-30" />
                  <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
                    {search ? t("tools.noMatching") : t("tools.noTools")}
                  </p>
                  {!search && <p className="text-xs mt-1">{t("tools.createHint")}</p>}
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {filteredMy.map((tool) => (
                    <ToolCard key={tool.id} tool={tool} onClick={() => navigate(`/tools/${tool.id}`)} />
                  ))}
                </div>
              )}
            </div>

            {/* Built-in Tools */}
            {filteredBuiltin.length > 0 && (
              <div>
                <button
                  onClick={() => setBuiltinCollapsed(!builtinCollapsed)}
                  className="flex items-center gap-1.5 text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-3 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                >
                  <span className={`transition-transform ${builtinCollapsed ? "" : "rotate-90"}`}>▶</span>
                  {t("tools.builtinTools")} ({filteredBuiltin.length})
                </button>
                {!builtinCollapsed && (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {filteredBuiltin.map((tool) => (
                      <ToolCard key={tool.id} tool={tool} onClick={() => navigate(`/tools/${tool.id}`)} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-2 px-4 py-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-xs text-red-600 dark:text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={clearError} className="text-red-400 hover:text-red-600 text-[10px]">{t("common.dismiss")}</button>
        </div>
      )}

      {/* Create dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setShowCreate(false)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80" onClick={(e) => e.stopPropagation()}>
            <p className="text-sm font-medium mb-3 text-gray-800 dark:text-gray-200">{t("tools.createTool")}</p>
            <div className="space-y-3">
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("tools.name")}</label>
                <input
                  autoFocus
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && createName.trim()) handleCreate(); if (e.key === "Escape") setShowCreate(false); }}
                  placeholder={t("tools.namePlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("tools.description")}</label>
                <input
                  value={createDesc}
                  onChange={(e) => setCreateDesc(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && createName.trim()) handleCreate(); if (e.key === "Escape") setShowCreate(false); }}
                  placeholder={t("tools.descPlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowCreate(false)} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">
                {t("common.cancel")}
              </button>
              <button
                onClick={handleCreate}
                disabled={!createName.trim() || creating}
                className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
              >
                {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : t("common.create")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
