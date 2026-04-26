import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Loader2,
  Globe,
  Server,
  Plus,
  Trash2,
  X,
  Search,
  Wrench,
  ChevronRight,
  Lock,
} from "lucide-react";
import { apiGet, getWorkspacePermissions, getWorkspaceId } from "../../lib/api-client";

interface McpTarget {
  name: string;
  description: string;
  category: string;
  status: string;
  type?: string;
}

interface ToolInfo {
  name: string;
  description: string;
}

interface McpTargetSelectorProps {
  selectedTargets: string[];
  onChange: (targets: string[]) => void;
  hasLegacyConfig?: boolean;
}

const CATEGORIES = [
  "all", "general", "observability", "security", "cost", "compute",
  "database", "messaging", "ai_ml", "search", "networking",
  "industry", "data", "devtools", "operations",
] as const;

// Targets that require IAM permissions (maps target name to required actions)
const IAM_REQUIRED_TARGETS: Record<string, string[]> = {
  // Monitoring
  cloudwatch: ["cloudwatch:DescribeAlarms", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "logs:DescribeLogGroups", "logs:StartQuery", "logs:GetQueryResults"],
  cloudtrail: ["cloudtrail:LookupEvents", "cloudtrail:StartQuery", "cloudtrail:GetQueryResults"],
  // Security
  iam: ["iam:GetUser", "iam:GetRole", "iam:ListRoles", "iam:ListPolicies"],
  kms: ["kms:Describe*", "kms:List*", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus"],
  acm: ["acm:Describe*", "acm:List*", "acm:GetCertificate"],
  guardduty: ["guardduty:Get*", "guardduty:List*"],
  "security-hub": ["securityhub:Get*", "securityhub:List*", "securityhub:BatchGet*"],
  inspector: ["inspector2:Get*", "inspector2:List*", "inspector2:BatchGet*"],
  config: ["config:Describe*", "config:Get*", "config:List*"],
  sts: ["sts:GetCallerIdentity"],
  // Compute
  ec2: ["ec2:Describe*"],
  lambda: ["lambda:GetFunction", "lambda:ListFunctions", "lambda:GetPolicy"],
  ecs: ["ecs:Describe*", "ecs:List*"],
  eks: ["eks:Describe*", "eks:List*"],
  autoscaling: ["autoscaling:Describe*"],
  // Storage
  "s3-readonly": ["s3:GetBucketLocation", "s3:GetBucketTagging", "s3:ListAllMyBuckets", "s3:ListBucket"],
  efs: ["elasticfilesystem:Describe*"],
  // Database
  rds: ["rds:Describe*", "rds:List*"],
  "dynamodb-readonly": ["dynamodb:Describe*", "dynamodb:List*"],
  elasticache: ["elasticache:Describe*", "elasticache:List*"],
  redshift: ["redshift:Describe*", "redshift:List*"],
  opensearch: ["es:Describe*", "es:List*"],
  // Networking
  route53: ["route53:Get*", "route53:List*"],
  elb: ["elasticloadbalancing:Describe*"],
  "api-gateway": ["apigateway:GET"],
  cloudfront: ["cloudfront:Get*", "cloudfront:List*"],
  // Messaging
  sns: ["sns:Get*", "sns:List*"],
  sqs: ["sqs:Get*", "sqs:List*"],
  eventbridge: ["events:Describe*", "events:List*"],
  "step-functions": ["states:Describe*", "states:List*", "states:GetExecutionHistory"],
  // Management
  cloudformation: ["cloudformation:Describe*", "cloudformation:List*", "cloudformation:GetTemplateSummary"],
  ssm: ["ssm:DescribeParameters", "ssm:GetParameter", "ssm:GetParameters", "ssm:List*"],
  "service-quotas": ["servicequotas:Get*", "servicequotas:List*"],
  health: ["health:Describe*"],
  "compute-optimizer": ["compute-optimizer:Get*"],
  // Cost
  "cost-explorer": ["ce:Get*", "ce:Describe*", "ce:List*"],
  "aws-pricing": ["pricing:GetProducts", "pricing:DescribeServices"],
  // Analytics
  athena: ["athena:Get*", "athena:List*", "athena:BatchGet*"],
  glue: ["glue:Get*", "glue:List*", "glue:BatchGet*"],
  kinesis: ["kinesis:Describe*", "kinesis:List*", "kinesis:Get*"],
  // AI/ML
  sagemaker: ["sagemaker:Describe*", "sagemaker:List*"],
  "bedrock-readonly": ["bedrock:Get*", "bedrock:List*"],
  // Identity
  cognito: ["cognito-idp:Describe*", "cognito-idp:List*"],
  // Backup
  backup: ["backup:Describe*", "backup:Get*", "backup:List*"],
  // Architecture
  "well-architected": ["wellarchitected:Get*", "wellarchitected:List*"],
  // Containers
  ecr: ["ecr:Describe*", "ecr:List*", "ecr:BatchGetImage"],
};

export default function McpTargetSelector({ selectedTargets, onChange, hasLegacyConfig }: McpTargetSelectorProps) {
  const { t } = useTranslation();
  const [allTargets, setAllTargets] = useState<McpTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [expandedTarget, setExpandedTarget] = useState<string | null>(null);

  useEffect(() => {
    apiGet<{ items: McpTarget[] }>("/mcp/targets")
      .then((data) => setAllTargets(data.items || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Normalize selectedTargets by stripping the "mcp-" prefix — the contract
  // for this field (and for workspace policy, backend mcp_targets_list, and
  // runtime resolution) is the short name ("cloudwatch", "nova-canvas").
  // Proposals authored before list_mcp_servers was fixed to return short
  // names may still carry "mcp-" prefixed values; normalize on render so
  // those drafts don't silently drop targets from the selector.
  const normalizedTargets = selectedTargets.map((n) =>
    n.startsWith("mcp-") ? n.slice("mcp-".length) : n,
  );

  // If normalization changed the array, propagate it up so persisted state
  // matches what the selector is displaying. Must be declared before any
  // conditional return to keep hook order stable across renders.
  useEffect(() => {
    const changed =
      normalizedTargets.length !== selectedTargets.length ||
      normalizedTargets.some((n, i) => n !== selectedTargets[i]);
    if (changed) onChange(normalizedTargets);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTargets.join(",")]);

  // Track which targets with IAM requirements are not yet granted
  const [deniedTargets, setDeniedTargets] = useState<Set<string>>(new Set());

  useEffect(() => {
    const allActions = Object.values(IAM_REQUIRED_TARGETS).flat();
    if (allActions.length === 0) return;
    const wsId = getWorkspaceId();
    getWorkspacePermissions(wsId, [...new Set(allActions)])
      .then((resp) => {
        if (!resp.hasRole) {
          // No role means all IAM-requiring targets are denied
          setDeniedTargets(new Set(Object.keys(IAM_REQUIRED_TARGETS)));
          return;
        }
        if (!resp.results) return;
        const resultMap = new Map(resp.results.map((r) => [r.action, r.allowed]));
        const denied = new Set<string>();
        for (const [target, actions] of Object.entries(IAM_REQUIRED_TARGETS)) {
          const hasMissing = actions.some((a) => !resultMap.get(a));
          if (hasMissing) denied.add(target);
        }
        setDeniedTargets(denied);
      })
      .catch(() => {
        // On error, don't block — just don't show lock icons
      });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
      </div>
    );
  }

  if (hasLegacyConfig) {
    return (
      <div className="px-3 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
        <p className="text-xs text-amber-700 dark:text-amber-300">{t("agentEdit.mcpLegacy")}</p>
      </div>
    );
  }

  const selectedSet = new Set(normalizedTargets);
  const selectedItems = allTargets.filter((t) => selectedSet.has(t.name));

  const removeTarget = (name: string) => {
    onChange(normalizedTargets.filter((t) => t !== name));
  };

  return (
    <div className="space-y-2">
      {/* Selected targets list */}
      {selectedItems.map((target) => {
        const isExpanded = expandedTarget === target.name;
        const displayName = target.name.replace(/^mcp-/, "");
        const isRemote = target.type === "remote";
        const isDenied = deniedTargets.has(target.name);
        return (
          <div
            key={target.name}
            className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
          >
            <div
              className="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer transition-colors"
              onClick={() => setExpandedTarget(isExpanded ? null : target.name)}
            >
              {isRemote ? (
                <Globe className="w-3.5 h-3.5 text-blue-500 shrink-0" />
              ) : (
                <Server className="w-3.5 h-3.5 text-purple-500 shrink-0" />
              )}
              <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">
                {displayName}
              </span>
              {isDenied && (
                <span title={t("iam.mcpIamRequired")}>
                  <Lock className="w-3 h-3 text-amber-500 shrink-0" />
                </span>
              )}
              <span className="text-[11px] text-gray-400 truncate flex-1">
                {target.description}
              </span>
              <ChevronRight
                className={`w-3 h-3 text-gray-400 shrink-0 transition-transform ${isExpanded ? "rotate-90" : ""}`}
              />
              <button
                onClick={(e) => { e.stopPropagation(); removeTarget(target.name); }}
                className="p-1 text-gray-300 hover:text-red-500 transition-colors"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
            {isExpanded && <ToolDetailInline targetName={target.name} />}
          </div>
        );
      })}

      {/* Add button — below the list */}
      <button
        onClick={() => setPickerOpen(true)}
        className="flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 px-3 py-2 border border-dashed border-blue-300 dark:border-blue-700 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors w-full justify-center"
      >
        <Plus className="w-3.5 h-3.5" /> {t("agentEdit.addMcp")}
      </button>

      {/* Picker modal */}
      <McpPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        allTargets={allTargets}
        selectedTargets={normalizedTargets}
        deniedTargets={deniedTargets}
        onToggle={(name) => {
          if (selectedSet.has(name)) {
            onChange(normalizedTargets.filter((t) => t !== name));
          } else {
            onChange([...normalizedTargets, name]);
          }
        }}
      />
    </div>
  );
}

/* ── Picker Modal ── */

function McpPicker({
  open,
  onClose,
  allTargets,
  selectedTargets,
  deniedTargets,
  onToggle,
}: {
  open: boolean;
  onClose: () => void;
  allTargets: McpTarget[];
  selectedTargets: string[];
  deniedTargets: Set<string>;
  onToggle: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [expandedTarget, setExpandedTarget] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) { setSearch(""); setCategory("all"); setExpandedTarget(null); }
  }, [open]);

  if (!open) return null;

  const selectedSet = new Set(selectedTargets);

  const catCounts = allTargets.reduce((acc, t) => {
    const c = t.category || "general";
    acc[c] = (acc[c] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const filtered = allTargets.filter((t) => {
    const matchSearch = !search.trim() ||
      t.name.toLowerCase().includes(search.toLowerCase()) ||
      t.description.toLowerCase().includes(search.toLowerCase());
    const matchCat = category === "all" || t.category === category;
    return matchSearch && matchCat;
  });

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-gray-900 rounded-xl w-[700px] max-h-[75vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-purple-500" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">
              {t("agentEdit.addMcp")}
            </span>
          </div>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 py-2 border-b border-gray-100 dark:border-gray-800">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("mcpPage.searchPlaceholder")}
              autoFocus
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            />
          </div>
        </div>

        {/* Category pills */}
        <div className="px-4 py-2 flex items-center gap-1.5 overflow-x-auto border-b border-gray-100 dark:border-gray-800">
          {CATEGORIES.map((cat) => {
            if (cat !== "all" && !catCounts[cat]) return null;
            const isActive = category === cat;
            return (
              <button
                key={cat}
                onClick={() => setCategory(cat)}
                className={`px-2.5 py-1 text-[11px] font-medium rounded-full whitespace-nowrap transition-colors shrink-0 ${
                  isActive
                    ? "bg-purple-500 text-white"
                    : "bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700"
                }`}
              >
                {cat === "all" ? t("mcpPage.allCategories") : t(`mcpPolicy.category.${cat}`)}
              </button>
            );
          })}
        </div>

        {/* Grid */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {filtered.length === 0 ? (
            <p className="text-center text-xs text-gray-400 py-8">{t("mcpPage.noMatch")}</p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {filtered.map((target) => {
                const isSelected = selectedSet.has(target.name);
                const isRemote = target.type === "remote";
                const displayName = target.name.replace(/^mcp-/, "");
                const isExpanded = expandedTarget === target.name;
                const isDenied = deniedTargets.has(target.name);

                return (
                  <div key={target.name} className={`rounded-lg border transition-all ${
                    isExpanded ? "col-span-2 border-purple-300 dark:border-purple-700" :
                    isSelected ? "border-purple-300 dark:border-purple-600 bg-purple-50/50 dark:bg-purple-900/20" :
                    "border-gray-200 dark:border-gray-700 hover:border-purple-300 dark:hover:border-purple-700"
                  }`}>
                    <div
                      className="flex items-center gap-2 p-3 cursor-pointer"
                      onClick={() => setExpandedTarget(isExpanded ? null : target.name)}
                    >
                      {isRemote ? (
                        <Globe className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                      ) : (
                        <Server className="w-3.5 h-3.5 text-purple-500 shrink-0" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">
                            {displayName}
                          </span>
                          {isDenied && (
                            <span title={t("iam.mcpIamRequired")}>
                              <Lock className="w-3 h-3 text-amber-500 shrink-0" />
                            </span>
                          )}
                          {isSelected && (
                            <span className="text-[10px] text-purple-500 shrink-0">{t("agentSkills.added")}</span>
                          )}
                        </div>
                        {target.description && (
                          <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-1 mt-0.5">
                            {target.description}
                          </p>
                        )}
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); onToggle(target.name); }}
                        className={`px-2 py-0.5 text-[10px] font-medium rounded shrink-0 transition-colors ${
                          isSelected
                            ? "text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20"
                            : "text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                        }`}
                      >
                        {isSelected ? t("common.delete") : t("common.add")}
                      </button>
                    </div>
                    {isExpanded && <ToolDetailInline targetName={target.name} />}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Inline tool detail ── */

function ToolDetailInline({ targetName }: { targetName: string }) {
  const { t } = useTranslation();
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiGet<{ tools: ToolInfo[] | string[] }>(`/mcp/targets/${encodeURIComponent(targetName)}/tools`)
      .then((data) => {
        if (cancelled) return;
        const raw = data.tools || [];
        setTools(raw.map((t: ToolInfo | string) => typeof t === "string" ? { name: t, description: "" } : t));
      })
      .catch(() => { if (!cancelled) setTools([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [targetName]);

  return (
    <div className="px-3 pb-3 border-t border-gray-100 dark:border-gray-800">
      <div className="pt-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-gray-400 py-1">
            <Loader2 className="w-3 h-3 animate-spin" /> {t("mcpPage.loadingTools")}
          </div>
        ) : tools && tools.length > 0 ? (
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {tools.map((tool) => (
              <div key={tool.name} className="flex items-start gap-2 py-1.5 px-2 rounded bg-gray-50 dark:bg-gray-800/50">
                <Wrench className="w-2.5 h-2.5 text-gray-400 mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-[11px] font-medium text-gray-900 dark:text-gray-100 font-mono">{tool.name}</p>
                  {tool.description && (
                    <p className="text-[10px] text-gray-500 dark:text-gray-400 line-clamp-1">{tool.description}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-gray-400 py-1">{t("mcpPage.noToolsAvailable")}</p>
        )}
      </div>
    </div>
  );
}
