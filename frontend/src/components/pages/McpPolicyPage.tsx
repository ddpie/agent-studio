import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Save, ChevronDown, ChevronRight } from "lucide-react";
import { apiGet, apiPut } from "../../lib/api-client";

interface McpTarget {
  name: string;
  description: string;
  category: string;
  status: string;
}

interface McpPolicy {
  mode: "all" | "allowlist" | "denylist";
  allowedTargets: string[];
  deniedTargets: string[];
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

export default function McpPolicyPage() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [policy, setPolicy] = useState<McpPolicy>({ mode: "all", allowedTargets: [], deniedTargets: [] });
  const [targets, setTargets] = useState<McpTarget[]>([]);
  const [selectedTargets, setSelectedTargets] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [policyData, targetsData] = await Promise.all([
        apiGet<McpPolicy>("/mcp/policy"),
        apiGet<{ items: McpTarget[] }>("/mcp/targets?all=true"),
      ]);
      setPolicy(policyData);
      setTargets(targetsData.items || []);

      // Initialize selected targets based on mode
      if (policyData.mode === "allowlist") {
        setSelectedTargets(new Set(policyData.allowedTargets));
      } else if (policyData.mode === "denylist") {
        setSelectedTargets(new Set(policyData.deniedTargets));
      }
    } catch (err) {
      console.error("Failed to load MCP policy:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleModeChange = (mode: "all" | "allowlist" | "denylist") => {
    setPolicy({ ...policy, mode });
    setSelectedTargets(new Set());
  };

  const toggleTarget = (targetName: string) => {
    const newSelected = new Set(selectedTargets);
    if (newSelected.has(targetName)) {
      newSelected.delete(targetName);
    } else {
      newSelected.add(targetName);
    }
    setSelectedTargets(newSelected);
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

  const handleSave = async () => {
    setSaving(true);
    try {
      const updatedPolicy: McpPolicy = {
        mode: policy.mode,
        allowedTargets: policy.mode === "allowlist" ? Array.from(selectedTargets) : [],
        deniedTargets: policy.mode === "denylist" ? Array.from(selectedTargets) : [],
      };
      await apiPut("/mcp/policy", { policy: updatedPolicy });
      setPolicy(updatedPolicy);
      // Show success toast (you can add a toast library if needed)
      console.log(t("mcpPolicy.saved"));
    } catch (err) {
      console.error("Failed to save policy:", err);
    } finally {
      setSaving(false);
    }
  };

  const targetsByCategory = targets.reduce((acc, target) => {
    const cat = target.category || "general";
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(target);
    return acc;
  }, {} as Record<string, McpTarget[]>);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">{t("mcpPolicy.title")}</h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Control which MCP tools are available in this workspace
        </p>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl space-y-6">
          {/* Mode Selector */}
          <div className="space-y-3">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Policy Mode
            </label>
            <div className="space-y-2">
              {/* All Tools */}
              <label className="flex items-start gap-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <input
                  type="radio"
                  name="mode"
                  checked={policy.mode === "all"}
                  onChange={() => handleModeChange("all")}
                  className="mt-0.5"
                />
                <div className="flex-1">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {t("mcpPolicy.mode.all")}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    {t("mcpPolicy.mode.allDesc")}
                  </div>
                </div>
              </label>

              {/* Allowlist */}
              <label className="flex items-start gap-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <input
                  type="radio"
                  name="mode"
                  checked={policy.mode === "allowlist"}
                  onChange={() => handleModeChange("allowlist")}
                  className="mt-0.5"
                />
                <div className="flex-1">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {t("mcpPolicy.mode.allowlist")}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    {t("mcpPolicy.mode.allowlistDesc")}
                  </div>
                </div>
              </label>

              {/* Denylist */}
              <label className="flex items-start gap-3 p-3 border border-gray-200 dark:border-gray-700 rounded-lg cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors">
                <input
                  type="radio"
                  name="mode"
                  checked={policy.mode === "denylist"}
                  onChange={() => handleModeChange("denylist")}
                  className="mt-0.5"
                />
                <div className="flex-1">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    {t("mcpPolicy.mode.denylist")}
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                    {t("mcpPolicy.mode.denylistDesc")}
                  </div>
                </div>
              </label>
            </div>
          </div>

          {/* Target Selection (only for allowlist/denylist) */}
          {policy.mode !== "all" && (
            <div className="space-y-3">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
                Select Tools
              </label>
              <div className="space-y-2">
                {CATEGORIES.map((category) => {
                  const categoryTargets = targetsByCategory[category] || [];
                  if (categoryTargets.length === 0) return null;

                  const isExpanded = expandedCategories.has(category);

                  return (
                    <div key={category} className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
                      {/* Category Header */}
                      <button
                        onClick={() => toggleCategory(category)}
                        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          {isExpanded ? (
                            <ChevronDown className="w-4 h-4 text-gray-400 dark:text-gray-500" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-gray-400 dark:text-gray-500" />
                          )}
                          <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                            {t(`mcpPolicy.category.${category}`)}
                          </span>
                          <span className="text-xs text-gray-400 dark:text-gray-500">({categoryTargets.length})</span>
                        </div>
                      </button>

                      {/* Category Targets */}
                      {isExpanded && (
                        <div className="p-3 space-y-2 bg-white dark:bg-gray-900">
                          {categoryTargets.map((target) => (
                            <label
                              key={target.name}
                              className="flex items-start gap-3 p-2 rounded hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer"
                            >
                              <input
                                type="checkbox"
                                checked={selectedTargets.has(target.name)}
                                onChange={() => toggleTarget(target.name)}
                                className="mt-0.5"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
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
                                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
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
            </div>
          )}

          {/* Save Button */}
          <div className="flex justify-end pt-4 border-t border-gray-200 dark:border-gray-700">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-purple-400 text-white text-sm font-medium rounded-lg transition-colors"
            >
              {saving ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="w-4 h-4" />
                  {t("mcpPolicy.save")}
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
