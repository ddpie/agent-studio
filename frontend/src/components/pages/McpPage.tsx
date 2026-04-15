import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  RefreshCw,
  Loader2,
  Search,
  Plug,
  Globe,
  Server,
  Wrench,
  ChevronRight,
} from "lucide-react";
import { apiGet } from "../../lib/api-client";

interface McpTarget {
  name: string;
  description: string;
  category: string;
  status: string;
  type?: string;
}

const CATEGORIES = [
  "all",
  "general",
  "observability",
  "security",
  "cost",
  "compute",
  "database",
  "messaging",
  "ai_ml",
  "search",
  "networking",
  "industry",
  "data",
  "devtools",
  "operations",
] as const;

const CAT_COLORS: Record<string, string> = {
  general: "bg-gray-500/10 text-gray-600 dark:text-gray-400",
  observability: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
  security: "bg-red-500/10 text-red-600 dark:text-red-400",
  cost: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
  compute: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
  database: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  messaging: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  ai_ml: "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
  search: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
  networking: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  industry: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
  data: "bg-lime-500/10 text-lime-600 dark:text-lime-400",
  devtools: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
  operations: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
};

export default function McpPage() {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<McpTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [expandedTarget, setExpandedTarget] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await apiGet<{ items: McpTarget[] }>("/mcp/targets");
      setTargets(data.items || []);
    } catch {
      setTargets([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = targets.filter((t) => {
    const matchSearch =
      !searchQuery.trim() ||
      t.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchCategory = category === "all" || t.category === category;
    return matchSearch && matchCategory;
  });

  const readyCount = targets.filter(
    (t) => t.status === "READY" || t.status === "ACTIVE"
  ).length;

  // Count per category for pills
  const catCounts = targets.reduce(
    (acc, t) => {
      const c = t.category || "general";
      acc[c] = (acc[c] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header — matches ToolLibraryPage style */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            MCP Tools
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {readyCount} {t("mcpPage.toolsAvailable")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              placeholder={t("mcpPage.searchPlaceholder")}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder:text-gray-400"
            />
          </div>
          <button
            onClick={load}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>

      {/* Category pills */}
      <div className="px-6 py-3 flex items-center gap-2 overflow-x-auto border-b border-gray-100 dark:border-gray-800">
        {CATEGORIES.map((cat) => {
          if (cat !== "all" && !catCounts[cat]) return null;
          const isActive = category === cat;
          const count = cat === "all" ? targets.length : catCounts[cat] || 0;
          return (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1.5 text-xs font-medium rounded-full whitespace-nowrap transition-colors shrink-0 ${
                isActive
                  ? "bg-purple-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
              }`}
            >
              {cat === "all"
                ? t("mcpPage.allCategories")
                : t(`mcpPolicy.category.${cat}`)}
              <span className="ml-1 opacity-60">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : targets.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <Plug className="w-12 h-12 mb-3 opacity-30" />
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {t("mcpPage.noTools")}
            </p>
            <p className="text-xs mt-1">{t("mcpPage.deployHint")}</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-400">
            <Search className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-sm">{t("mcpPage.noMatch")}</p>
          </div>
        ) : (
          <div
            className="grid gap-4"
            style={{
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            }}
          >
            {filtered.map((target) => (
              <TargetCard
                key={target.name}
                target={target}
                isOpen={expandedTarget === target.name}
                onToggle={() =>
                  setExpandedTarget(
                    expandedTarget === target.name ? null : target.name
                  )
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface ToolInfo {
  name: string;
  description: string;
}

function TargetCard({
  target,
  isOpen,
  onToggle,
}: {
  target: McpTarget;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const isReady = target.status === "READY" || target.status === "ACTIVE";
  const isRemote = target.type === "remote";
  const catColor = CAT_COLORS[target.category] || CAT_COLORS.general;
  const displayName = target.name.replace(/^mcp-/, "");

  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [loadingTools, setLoadingTools] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    if (tools !== null) return;

    let cancelled = false;
    setLoadingTools(true);
    apiGet<{ tools: ToolInfo[] | string[] }>(
      `/mcp/targets/${encodeURIComponent(target.name)}/tools`
    )
      .then((data) => {
        if (cancelled) return;
        const raw = data.tools || [];
        // Handle both [{name, description}] and [string] formats
        const normalized: ToolInfo[] = raw.map((t: ToolInfo | string) =>
          typeof t === "string" ? { name: t, description: "" } : t
        );
        setTools(normalized);
      })
      .catch(() => {
        if (!cancelled) setTools([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingTools(false);
      });
    return () => { cancelled = true; };
  }, [isOpen, target.name, tools]);

  return (
    <div
      data-testid={`mcp-card-${target.name}`}
      className={`group relative flex flex-col rounded-xl border transition-all duration-200 ${
        isOpen
          ? "border-purple-300 dark:border-purple-700 shadow-lg shadow-purple-500/5 col-span-full"
          : "border-gray-200 dark:border-gray-800 hover:border-purple-300 dark:hover:border-purple-700 hover:shadow-md hover:shadow-purple-500/5"
      } bg-white dark:bg-gray-900`}
    >
      {/* Card header (always visible) */}
      <div className="p-5 cursor-pointer" onClick={onToggle}>
        <div className="flex items-start gap-3">
          <div
            className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${catColor}`}
          >
            {isRemote ? (
              <Globe className="w-5 h-5" />
            ) : (
              <Server className="w-5 h-5" />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-50 truncate">
                {displayName}
              </h3>
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  isReady ? "bg-green-500" : "bg-gray-300 dark:bg-gray-600"
                }`}
              />
              {tools && (
                <span className="text-[10px] text-gray-400">
                  {tools.length} {t("mcpPage.tools")}
                </span>
              )}
            </div>
            {target.description && (
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2 leading-relaxed">
                {target.description}
              </p>
            )}
          </div>
          <ChevronRight
            className={`w-4 h-4 text-gray-400 shrink-0 transition-transform ${
              isOpen ? "rotate-90" : "opacity-0 group-hover:opacity-100"
            }`}
          />
        </div>

        {/* Tags */}
        <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
          <span
            className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${catColor}`}
          >
            {target.category}
          </span>
          {isRemote && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400">
              remote
            </span>
          )}
        </div>
      </div>

      {/* Inline tool list (expanded) */}
      {isOpen && (
        <div className="px-5 pb-5 pt-0 border-t border-gray-100 dark:border-gray-800">
          <div className="pt-4">
            {loadingTools ? (
              <div className="flex items-center gap-2 text-xs text-gray-400 py-2">
                <Loader2 className="w-3 h-3 animate-spin" /> {t("mcpPage.loadingTools")}
              </div>
            ) : tools && tools.length > 0 ? (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {tools.map((tool) => (
                  <div
                    key={tool.name}
                    className="flex items-start gap-3 py-2 px-3 rounded-lg bg-gray-50 dark:bg-gray-800/50"
                  >
                    <Wrench className="w-3 h-3 text-gray-400 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-gray-900 dark:text-gray-100 font-mono">
                        {tool.name}
                      </p>
                      {tool.description && (
                        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-2">
                          {tool.description}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-400 py-2">{t("mcpPage.noToolsAvailable")}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// end of file
