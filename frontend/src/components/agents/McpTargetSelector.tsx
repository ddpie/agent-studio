import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { apiGet } from "../../lib/api-client";

interface McpTarget {
  name: string;
  description: string;
  category: string;
  status: string;
}

interface McpTargetSelectorProps {
  selectedTargets: string[];
  onChange: (targets: string[]) => void;
  hasLegacyConfig?: boolean;
}

const CATEGORIES = [
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
  "general",
] as const;

export default function McpTargetSelector({ selectedTargets, onChange, hasLegacyConfig }: McpTargetSelectorProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [targets, setTargets] = useState<McpTarget[]>([]);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadTargets();
  }, []);

  const loadTargets = async () => {
    setLoading(true);
    try {
      const data = await apiGet<{ items: McpTarget[] }>("/mcp/targets");
      setTargets(data.items || []);
    } catch (err) {
      console.error("Failed to load MCP targets:", err);
    } finally {
      setLoading(false);
    }
  };

  const toggleTarget = (targetName: string) => {
    const newSelected = new Set(selectedTargets);
    if (newSelected.has(targetName)) {
      newSelected.delete(targetName);
    } else {
      newSelected.add(targetName);
    }
    onChange(Array.from(newSelected));
  };

  const toggleCategory = (category: string) => {
    const newExpanded = new Set(expandedCategories);
    if (newExpanded.has(category)) {
      newExpanded.delete(category);
    } else {
      newExpanded.add(category);
    }
    setExpandedCategories(newExpanded);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
      </div>
    );
  }

  if (targets.length === 0) {
    return null;
  }

  if (hasLegacyConfig) {
    return (
      <div className="px-3 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {t("agentEdit.mcpLegacy")}
        </p>
      </div>
    );
  }

  const targetsByCategory = targets.reduce((acc, target) => {
    const cat = target.category || "general";
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(target);
    return acc;
  }, {} as Record<string, McpTarget[]>);

  return (
    <div className="space-y-2">
      {CATEGORIES.map((category) => {
        const categoryTargets = targetsByCategory[category] || [];
        if (categoryTargets.length === 0) return null;

        const isExpanded = expandedCategories.has(category);

        return (
          <div key={category} className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
            <button
              onClick={() => toggleCategory(category)}
              className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              <div className="flex items-center gap-2">
                {isExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
                )}
                <span className="text-xs font-medium text-gray-900 dark:text-gray-100">
                  {t(`mcpPolicy.category.${category}`)}
                </span>
                <span className="text-[10px] text-gray-400">({categoryTargets.length})</span>
              </div>
            </button>

            {isExpanded && (
              <div className="p-2 space-y-1 bg-white dark:bg-gray-900">
                {categoryTargets.map((target) => (
                  <label
                    key={target.name}
                    className="flex items-start gap-2 p-2 rounded hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedTargets.includes(target.name)}
                      onChange={() => toggleTarget(target.name)}
                      className="mt-0.5 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-gray-900 dark:text-gray-100">
                          {target.name}
                        </span>
                        <span
                          className={`text-[10px] px-1.5 py-0.5 rounded ${
                            target.status === "ACTIVE"
                              ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
                              : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400"
                          }`}
                        >
                          {target.status}
                        </span>
                      </div>
                      {target.description && (
                        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
                          {target.description}
                        </p>
                      )}
                    </div>
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
