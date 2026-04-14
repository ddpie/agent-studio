import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Plug,
  RefreshCw,
  Loader2,
  Search,
  ChevronDown,
  ChevronRight,
  Wrench,
  Globe,
  Server,
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

export default function McpPage() {
  const { t } = useTranslation();
  const [targets, setTargets] = useState<McpTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
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

  const filtered = search.trim()
    ? targets.filter(
        (t) =>
          t.name.toLowerCase().includes(search.toLowerCase()) ||
          t.description.toLowerCase().includes(search.toLowerCase())
      )
    : targets;

  const byCategory = filtered.reduce(
    (acc, target) => {
      const cat = target.category || "general";
      if (!acc[cat]) acc[cat] = [];
      acc[cat].push(target);
      return acc;
    },
    {} as Record<string, McpTarget[]>
  );

  const toggleCategory = (cat: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  const readyCount = targets.filter((t) => t.status === "READY" || t.status === "ACTIVE").length;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            MCP Tools
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {readyCount}/{targets.length} {t("common.available")}
          </p>
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

      {/* Search */}
      <div className="px-6 py-3 border-b border-gray-100 dark:border-gray-800">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            type="text"
            placeholder="Search tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-1 focus:ring-purple-500 text-gray-900 dark:text-gray-100 placeholder-gray-400"
          />
        </div>
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
              No MCP tools available
            </p>
            <p className="text-xs mt-1">
              Deploy MCP runtimes with scripts/deploy-mcp.sh
            </p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <Search className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-sm">No tools match "{search}"</p>
          </div>
        ) : (
          <div className="space-y-2">
            {CATEGORIES.map((category) => {
              const categoryTargets = byCategory[category];
              if (!categoryTargets?.length) return null;

              const isExpanded = expandedCategories.has(category);

              return (
                <div
                  key={category}
                  className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
                >
                  {/* Category header */}
                  <button
                    onClick={() => toggleCategory(category)}
                    className="w-full flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-gray-800/60 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      {isExpanded ? (
                        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
                      ) : (
                        <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
                      )}
                      <span className="text-xs font-semibold text-gray-900 dark:text-gray-100">
                        {t(`mcpPolicy.category.${category}`)}
                      </span>
                      <span className="text-[10px] text-gray-400">
                        ({categoryTargets.length})
                      </span>
                    </div>
                  </button>

                  {/* Target cards */}
                  {isExpanded && (
                    <div className="p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 bg-white dark:bg-gray-900/50">
                      {categoryTargets.map((target) => {
                        const isReady =
                          target.status === "READY" || target.status === "ACTIVE";
                        const isRemote = target.type === "remote";
                        const isOpen = expandedTarget === target.name;

                        return (
                          <button
                            key={target.name}
                            onClick={() =>
                              setExpandedTarget(isOpen ? null : target.name)
                            }
                            className={`text-left p-3 rounded-lg border transition-colors ${
                              isOpen
                                ? "border-purple-300 dark:border-purple-700 bg-purple-50/50 dark:bg-purple-900/10"
                                : "border-gray-100 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 bg-white dark:bg-gray-900"
                            }`}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <div className="flex items-center gap-1.5">
                                {isRemote ? (
                                  <Globe className="w-3 h-3 text-blue-500" />
                                ) : (
                                  <Server className="w-3 h-3 text-purple-500" />
                                )}
                                <span className="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">
                                  {target.name.replace(/^mcp-/, "")}
                                </span>
                              </div>
                              <span
                                className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                                  isReady ? "bg-green-500" : "bg-gray-300 dark:bg-gray-600"
                                }`}
                              />
                            </div>
                            {target.description && (
                              <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2">
                                {target.description}
                              </p>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Expanded target detail panel */}
      {expandedTarget && (
        <TargetDetail
          targetName={expandedTarget}
          onClose={() => setExpandedTarget(null)}
        />
      )}
    </div>
  );
}

function TargetDetail({
  targetName,
  onClose,
}: {
  targetName: string;
  onClose: () => void;
}) {
  const [tools, setTools] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTools(null);

    apiGet<{ tools: string[] }>(`/mcp/targets/${encodeURIComponent(targetName)}/tools`)
      .then((data) => {
        if (!cancelled) setTools(data.tools || []);
      })
      .catch(() => {
        if (!cancelled) setTools([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [targetName]);

  return (
    <div className="border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-6 py-4 max-h-64 overflow-y-auto">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Wrench className="w-3.5 h-3.5 text-purple-500" />
          <span className="text-xs font-semibold text-gray-900 dark:text-gray-100">
            {targetName.replace(/^mcp-/, "")}
          </span>
          {tools && (
            <span className="text-[10px] text-gray-400">
              {tools.length} tools
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
        >
          Close
        </button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading tools...
        </div>
      ) : tools && tools.length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1">
          {tools.map((tool) => (
            <div
              key={tool}
              className="text-[11px] text-gray-700 dark:text-gray-300 px-2 py-1 bg-white dark:bg-gray-900 rounded border border-gray-100 dark:border-gray-700 truncate"
              title={tool}
            >
              {tool}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-gray-400">No tools available</p>
      )}
    </div>
  );
}
