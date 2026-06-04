import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Shield,
  CheckCircle2,
  Lock,
  RefreshCw,
  Loader2,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  AlertTriangle,
} from "lucide-react";
import {
  createWorkspaceRole,
  getWorkspacePermissions,
  grantMcpTargets,
  isPlatformAdmin,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { MCP_TARGETS, type McpTargetDef } from "../../generated/mcp-targets";

// ── Permission status per target ──

interface TargetStatus {
  granted: boolean;
  missingActions: string[];
  loading: boolean;
}

interface IamPermissionsTabProps {
  readOnly?: boolean;
  /** When provided, operate on this workspace instead of the store's currentWorkspace. */
  workspaceId?: string;
  /** Callback fired after a workspace IAM role is successfully created. */
  onRoleCreated?: () => void;
}

export default function IamPermissionsTab({ readOnly = false, workspaceId, onRoleCreated }: IamPermissionsTabProps) {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  const wsId = workspaceId ?? currentWorkspace?.workspaceId;

  const [isAdmin, setIsAdmin] = useState(false);
  const [roleArn, setRoleArn] = useState<string | null>(null);
  const [hasRole, setHasRole] = useState(false);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [targetStatuses, setTargetStatuses] = useState<Record<string, TargetStatus>>({});
  const [grantingTargets, setGrantingTargets] = useState<Set<string>>(new Set());
  const [expandedTargets, setExpandedTargets] = useState<Set<string>>(new Set());
  const [copiedTarget, setCopiedTarget] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<McpTargetDef | null>(null);
  const [sensitivityOpen, setSensitivityOpen] = useState<string | null>(null);

  useEffect(() => { isPlatformAdmin().then(setIsAdmin); }, []);

  // Collect all actions from all targets that have iamPolicy
  const getAllActions = useCallback((): string[] => {
    const actions: string[] = [];
    for (const target of MCP_TARGETS) {
      if (target.iamPolicy) {
        for (const stmt of target.iamPolicy.Statement) {
          actions.push(...stmt.Action);
        }
      }
    }
    return [...new Set(actions)];
  }, []);

  // Check permissions for all targets
  const checkPermissions = useCallback(async (showRefresh = false) => {
    if (!wsId) return;
    if (showRefresh) setRefreshing(true);
    else setLoading(true);

    try {
      const allActions = getAllActions();
      const resp = await getWorkspacePermissions(wsId, allActions);
      setHasRole(resp.hasRole);
      setRoleArn(resp.roleArn ?? null);

      if (resp.hasRole && resp.results) {
        const resultMap = new Map<string, boolean>();
        for (const r of resp.results) {
          resultMap.set(r.action, r.allowed);
        }

        const statuses: Record<string, TargetStatus> = {};
        for (const target of MCP_TARGETS) {
          if (!target.iamPolicy) {
            statuses[target.name] = { granted: true, missingActions: [], loading: false };
          } else {
            const missing: string[] = [];
            for (const stmt of target.iamPolicy.Statement) {
              for (const action of stmt.Action) {
                if (!resultMap.get(action)) {
                  missing.push(action);
                }
              }
            }
            statuses[target.name] = {
              granted: missing.length === 0,
              missingActions: missing,
              loading: false,
            };
          }
        }
        setTargetStatuses(statuses);
      }
    } catch {
      // If permissions API fails, still set hasRole = false
      setHasRole(false);
      setRoleArn(null);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [wsId, getAllActions]);

  useEffect(() => {
    checkPermissions();
  }, [checkPermissions]);

  const handleCreateRole = async () => {
    if (!wsId) return;
    setCreating(true);
    try {
      const resp = await createWorkspaceRole(wsId);
      setRoleArn(resp.roleArn);
      setHasRole(true);
      toast.success(t("iam.roleCreated"));
      onRoleCreated?.();
      // Re-check permissions after role creation
      await checkPermissions();
    } catch {
      const msg = err instanceof Error ? err.message : t("iam.createFailed");
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  };

  const requestGrant = (target: McpTargetDef) => {
    if (target.sensitivity === "medium" || target.sensitivity === "high") {
      setConfirmTarget(target);
    } else {
      doGrant(target.name);
    }
  };

  const doGrant = async (targetName: string) => {
    if (!wsId) return;
    setConfirmTarget(null);
    setGrantingTargets((prev) => new Set(prev).add(targetName));
    try {
      await grantMcpTargets(wsId, [targetName]);
      toast.success(t("iam.grantSuccess", { target: targetName }));
      await new Promise((r) => setTimeout(r, 3000));
      await checkPermissions(true);
    } catch {
      const msg = err instanceof Error ? err.message : t("iam.grantFailed");
      toast.error(msg);
    } finally {
      setGrantingTargets((prev) => {
        const next = new Set(prev);
        next.delete(targetName);
        return next;
      });
    }
  };

  const toggleExpanded = (name: string) => {
    setExpandedTargets((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const copyToClipboard = (text: string, targetName: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedTarget(targetName);
      setTimeout(() => setCopiedTarget(null), 2000);
    });
  };

  const buildCliCommand = (target: McpTargetDef): string => {
    if (!target.iamPolicy || !roleArn) return "";
    const roleName = roleArn.split("/").pop() || "";
    const policyDoc = JSON.stringify(
      { Version: "2012-10-17", Statement: target.iamPolicy.Statement },
      null,
      2,
    );
    return `aws iam put-role-policy \\\n  --role-name ${roleName} \\\n  --policy-name MCP-${target.name} \\\n  --policy-document '${policyDoc}'`;
  };

  if (!wsId) {
    return (
      <div className="text-xs text-gray-500 dark:text-gray-400 p-4">
        {t("workspace.members.noWorkspace")}
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
      </div>
    );
  }

  // ── No role state ──
  if (!hasRole) {
    return (
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Shield className="w-3.5 h-3.5" /> {t("iam.title")}
        </h3>
        <div className="rounded-lg border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 p-4">
          <p className="text-xs text-gray-700 dark:text-gray-300 mb-3">
            {t("iam.noRoleDesc")}
          </p>
          {readOnly ? (
            <p className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-1">
              <Lock className="w-3 h-3" />
              <a href="#/admin" className="hover:underline">{t("iam.goToAdmin")}</a>
            </p>
          ) : isAdmin ? (
            <button
              onClick={handleCreateRole}
              disabled={creating}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {creating ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Shield className="w-3.5 h-3.5" />
              )}
              {creating ? t("iam.creating") : t("iam.createRole")}
            </button>
          ) : (
            <p className="text-xs text-amber-700 dark:text-amber-400 flex items-center gap-1">
              <Lock className="w-3 h-3" /> {t("iam.contactAdmin")}
            </p>
          )}
        </div>
      </section>
    );
  }

  // ── Has role state ──
  return (
    <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-4">
      <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
        <Shield className="w-3.5 h-3.5" /> {t("iam.title")}
      </h3>

      {/* Role info */}
      <div className="space-y-2 text-[11px]">
        <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
          <span className="text-gray-500 dark:text-gray-400">{t("iam.roleArn")}</span>
          <span className="font-mono text-gray-700 dark:text-gray-300 truncate" title={roleArn ?? ""}>
            {roleArn}
          </span>
        </div>
        <div className="border-t border-gray-100 dark:border-gray-800 pt-2">
          <div className="flex justify-between gap-2">
            <span className="text-gray-500 dark:text-gray-400">{t("iam.permissionBoundary")}</span>
            <button
              onClick={() => setExpandedTargets((prev) => {
                const next = new Set(prev);
                if (next.has("__boundary__")) next.delete("__boundary__");
                else next.add("__boundary__");
                return next;
              })}
              className="flex items-center gap-1 text-green-600 dark:text-green-400 hover:underline cursor-pointer"
            >
              <CheckCircle2 className="w-3 h-3" /> AgentStudioWorkspaceCeiling
              {expandedTargets.has("__boundary__") ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            </button>
          </div>
          {expandedTargets.has("__boundary__") && (
            <div className="mt-2 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg text-[10px] space-y-2">
              <p className="text-gray-600 dark:text-gray-400 font-medium">{t("iam.boundaryDesc")}</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                {[
                  { cat: t("iam.boundaryCat.compute"), items: "EC2, Lambda, ECS, EKS, Auto Scaling" },
                  { cat: t("iam.boundaryCat.storage"), items: "S3 (list/location), EFS" },
                  { cat: t("iam.boundaryCat.database"), items: "RDS, DynamoDB, ElastiCache, Redshift, OpenSearch" },
                  { cat: t("iam.boundaryCat.security"), items: "IAM, KMS, ACM, GuardDuty, Security Hub, Inspector, Config" },
                  { cat: t("iam.boundaryCat.networking"), items: "Route 53, ELB, CloudFront" },
                  { cat: t("iam.boundaryCat.monitoring"), items: "CloudWatch, CloudTrail, Performance Insights" },
                  { cat: t("iam.boundaryCat.messaging"), items: "SNS, SQS, EventBridge, Step Functions" },
                  { cat: t("iam.boundaryCat.management"), items: "CloudFormation, SSM, Service Quotas, Health" },
                  { cat: t("iam.boundaryCat.analytics"), items: "Athena, Glue, Kinesis, Firehose" },
                  { cat: t("iam.boundaryCat.cost"), items: "Cost Explorer, Pricing, Budgets" },
                  { cat: t("iam.boundaryCat.aiml"), items: "Bedrock, SageMaker" },
                  { cat: t("iam.boundaryCat.other"), items: "Well-Architected, Cognito, Backup, STS" },
                ].map(({ cat, items }) => (
                  <div key={cat}>
                    <span className="font-semibold text-gray-700 dark:text-gray-300">{cat}: </span>
                    <span className="text-gray-500 dark:text-gray-400">{items}</span>
                  </div>
                ))}
              </div>
              <p className="text-gray-500 dark:text-gray-400 italic">{t("iam.boundaryReadOnly")}</p>
            </div>
          )}
        </div>
      </div>

      {/* Permission Boundary */}

      {/* MCP authorization list */}
      <div>
        <h4 className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide mb-2">
          {t("iam.mcpAuth")}
        </h4>
        <p className="text-[10px] text-gray-400 dark:text-gray-500 mb-3">
          {t("iam.scpNote")}
        </p>

        <div className="space-y-1.5">
          {MCP_TARGETS.map((target) => {
            const status = targetStatuses[target.name];
            const isGranted = !target.iamPolicy || status?.granted;
            const missingCount = status?.missingActions.length ?? 0;
            const isExpanded = expandedTargets.has(target.name);
            const isGranting = grantingTargets.has(target.name);
            const noIamNeeded = !target.iamPolicy;
            const totalActions = target.iamPolicy?.Statement.flatMap((s) => s.Action).length ?? 0;
            const grantedCount = totalActions - missingCount;

            return (
              <div
                key={target.name}
                className="border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden"
              >
                <div className="px-3 py-2 space-y-1.5">
                  {/* Row 1: icon + name + badge + grant button */}
                  <div className="flex items-center gap-2">
                    {isGranted ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />
                    ) : (
                      <Lock className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                    )}
                    <span className="text-xs font-medium text-gray-800 dark:text-gray-200">
                      {target.displayName}
                    </span>
                    {target.sensitivity === "medium" && target.sensitiveReasons.length > 0 && (
                      <span
                        role="button"
                        onClick={() => setSensitivityOpen(sensitivityOpen === target.name ? null : target.name)}
                        className="inline-flex items-center gap-0.5 text-[10px] leading-tight text-amber-600 dark:text-amber-400 hover:underline cursor-pointer"
                      >
                        <AlertTriangle className="w-2.5 h-2.5" /> {t("iam.sensitivityMedium")}
                      </span>
                    )}
                    {target.sensitivity === "high" && target.sensitiveReasons.length > 0 && (
                      <span
                        role="button"
                        onClick={() => setSensitivityOpen(sensitivityOpen === target.name ? null : target.name)}
                        className="inline-flex items-center gap-0.5 text-[10px] leading-tight text-red-600 dark:text-red-400 hover:underline cursor-pointer"
                      >
                        <AlertTriangle className="w-2.5 h-2.5" /> {t("iam.sensitivityHigh")}
                      </span>
                    )}
                    <span className="flex-1" />
                    {!isGranted && !noIamNeeded && !readOnly && isAdmin && (
                      <button
                        onClick={() => requestGrant(target)}
                        disabled={isGranting}
                        className="flex items-center gap-1 px-2 py-0.5 text-[11px] leading-tight font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded-md transition-colors disabled:opacity-50"
                      >
                        {isGranting ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                        {t("iam.autoGrant")}
                      </button>
                    )}
                    {!isGranted && !noIamNeeded && readOnly && (
                      <a href="#/admin" className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline">
                        {t("iam.goToAdmin")}
                      </a>
                    )}
                  </div>

                  {/* Row 2: progress bar + status text (clickable to expand) */}
                  {noIamNeeded ? (
                    <div className="pl-[22px] text-[10px] text-gray-400 dark:text-gray-500">
                      {t("iam.noExtraPerms")}
                    </div>
                  ) : (
                    <button
                      onClick={() => toggleExpanded(target.name)}
                      className="flex items-center gap-2 pl-[22px] w-full group cursor-pointer"
                    >
                      {/* Progress bar */}
                      <div className="w-20 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden shrink-0">
                        <div
                          className={`h-full rounded-full transition-all ${isGranted ? "bg-green-500" : "bg-amber-500"}`}
                          style={{ width: `${totalActions > 0 ? (grantedCount / totalActions) * 100 : 0}%` }}
                        />
                      </div>
                      <span className={`text-[10px] group-hover:underline ${
                        isGranted ? "text-green-600 dark:text-green-400" : "text-amber-600 dark:text-amber-400"
                      }`}>
                        {grantedCount}/{totalActions}
                        {isGranted ? ` ${t("iam.allPermsGranted")}` : ` · ${t("iam.missingPerms", { count: missingCount })}`}
                      </span>
                      {isExpanded ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />}
                    </button>
                  )}
                </div>

                {/* Sensitivity reasons popover */}
                {sensitivityOpen === target.name && target.sensitiveReasons.length > 0 && (
                  <div className={`px-3 py-2 border-t text-[11px] ${
                    target.sensitivity === "high"
                      ? "border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30"
                      : "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30"
                  }`}>
                    <ul className="space-y-1">
                      {target.sensitiveReasons.map((reason, i) => (
                        <li key={i} className={`flex items-start gap-1.5 ${
                          target.sensitivity === "high"
                            ? "text-red-700 dark:text-red-300"
                            : "text-amber-700 dark:text-amber-300"
                        }`}>
                          <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                          <span>{reason}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Expanded detail section */}
                {isExpanded && !noIamNeeded && target.iamPolicy && (
                  <div className="px-3 pb-3 border-t border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50">
                    <div className="pt-3 space-y-3">
                      {/* Actions list — green for granted, red for missing */}
                      <div>
                        <p className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide mb-1">
                          {isGranted ? t("iam.grantedActions") : `${t("iam.grantedActions")} / ${t("iam.missingActions")}`}
                        </p>
                        <div className="flex flex-wrap gap-1">
                          {target.iamPolicy.Statement.flatMap((s) => s.Action).map((action) => {
                            const isMissing = status?.missingActions.includes(action);
                            return (
                              <span
                                key={action}
                                className={`px-1.5 py-0.5 text-[10px] font-mono rounded border ${
                                  isMissing
                                    ? "bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border-red-200 dark:border-red-800"
                                    : "bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 border-green-200 dark:border-green-800"
                                }`}
                              >
                                {action}
                              </span>
                            );
                          })}
                        </div>
                      </div>

                      {/* JSON Policy */}
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <p className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">
                            {t("iam.jsonPolicy")}
                          </p>
                          <button
                            onClick={() => copyToClipboard(JSON.stringify({ Version: "2012-10-17", ...target.iamPolicy }, null, 2), target.name + "-json")}
                            className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                          >
                            {copiedTarget === target.name + "-json" ? (
                              <><Check className="w-3 h-3" /> {t("iam.copied")}</>
                            ) : (
                              <><Copy className="w-3 h-3" /> {t("common.copy")}</>
                            )}
                          </button>
                        </div>
                        <pre className="text-[11px] font-mono bg-gray-900 dark:bg-gray-950 text-blue-300 p-3 rounded-lg overflow-x-auto leading-relaxed whitespace-pre-wrap">
                          {JSON.stringify({ Version: "2012-10-17", ...target.iamPolicy }, null, 2)}
                        </pre>
                      </div>

                      {/* CLI command */}
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <p className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">
                            {t("iam.cliCommand")}
                          </p>
                          <button
                            onClick={() => copyToClipboard(buildCliCommand(target), target.name + "-cli")}
                            className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                          >
                            {copiedTarget === target.name + "-cli" ? (
                              <><Check className="w-3 h-3" /> {t("iam.copied")}</>
                            ) : (
                              <><Copy className="w-3 h-3" /> {t("common.copy")}</>
                            )}
                          </button>
                        </div>
                        <pre className="text-[11px] font-mono bg-gray-900 dark:bg-gray-950 text-green-400 p-3 rounded-lg overflow-x-auto leading-relaxed whitespace-pre-wrap">
                          {buildCliCommand(target)}
                        </pre>
                      </div>

                      {/* Console steps */}
                      <div>
                        <p className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide mb-1">
                          {t("iam.consoleSteps")}
                        </p>
                        <ol className="text-[11px] text-gray-600 dark:text-gray-400 space-y-1 list-decimal list-inside">
                          <li>{t("iam.consoleStep1")}</li>
                          <li>{t("iam.consoleStep2", { roleName: roleArn?.split("/").pop() || "" })}</li>
                          <li>{t("iam.consoleStep3")}</li>
                          <li>{t("iam.consoleStep4")}</li>
                        </ol>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Refresh button */}
      <div className="flex justify-end pt-1">
        <button
          onClick={() => checkPermissions(true)}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg disabled:opacity-50 transition-colors"
        >
          {refreshing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
          {t("iam.refresh")}
        </button>
      </div>

      {/* Sensitivity confirmation modal */}
      {confirmTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setConfirmTarget(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-md w-full mx-4 p-5 space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              <h3 className="text-sm font-semibold">{t("iam.confirmGrantTitle")}</h3>
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-300">
              {t("iam.confirmGrantDesc", { target: confirmTarget.displayName })}
            </p>
            <ul className="space-y-1.5">
              {confirmTarget.sensitiveReasons.map((reason, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
            {confirmTarget.iamPolicy && (
              <div className="flex flex-wrap gap-1 pt-1">
                {confirmTarget.iamPolicy.Statement.flatMap((s) => s.Action).map((a) => (
                  <span key={a} className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800">
                    {a}
                  </span>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setConfirmTarget(null)}
                className="px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-lg transition-colors"
              >
                {t("iam.confirmGrantCancel")}
              </button>
              <button
                onClick={() => doGrant(confirmTarget.name)}
                className="px-3 py-1.5 text-xs bg-amber-600 hover:bg-amber-700 text-white rounded-lg transition-colors"
              >
                {t("iam.confirmGrantBtn")}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
