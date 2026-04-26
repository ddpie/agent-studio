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
} from "lucide-react";
import {
  createWorkspaceRole,
  getWorkspacePermissions,
  grantMcpTargets,
  isPlatformAdmin,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { useWorkspaceStore } from "../../stores/workspace-store";

// ── Phase 1 hardcoded MCP targets ──

interface McpTargetDef {
  name: string;
  displayName: string;
  iamPolicy: { Statement: { Effect: string; Action: string[]; Resource: string }[] } | null;
}

// Helper to reduce boilerplate — most targets follow the same shape.
function t_(name: string, displayName: string, actions: string[]): McpTargetDef {
  return { name, displayName, iamPolicy: { Statement: [{ Effect: "Allow", Action: actions, Resource: "*" }] } };
}

const MCP_TARGETS: McpTargetDef[] = [
  // No IAM needed
  { name: "aws-knowledge", displayName: "AWS Knowledge", iamPolicy: null },
  { name: "aws-api", displayName: "AWS API", iamPolicy: null },
  // Monitoring & observability
  t_("cloudwatch", "CloudWatch", ["cloudwatch:DescribeAlarms", "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "logs:DescribeLogGroups", "logs:StartQuery", "logs:GetQueryResults"]),
  t_("cloudtrail", "CloudTrail", ["cloudtrail:LookupEvents", "cloudtrail:StartQuery", "cloudtrail:GetQueryResults"]),
  // Security & identity
  t_("iam", "IAM", ["iam:GetUser", "iam:GetRole", "iam:ListRoles", "iam:ListPolicies"]),
  t_("kms", "KMS", ["kms:Describe*", "kms:List*", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus"]),
  t_("acm", "ACM (Certificates)", ["acm:Describe*", "acm:List*", "acm:GetCertificate"]),
  t_("guardduty", "GuardDuty", ["guardduty:Get*", "guardduty:List*"]),
  t_("security-hub", "Security Hub", ["securityhub:Get*", "securityhub:List*", "securityhub:BatchGet*"]),
  t_("inspector", "Inspector", ["inspector2:Get*", "inspector2:List*", "inspector2:BatchGet*"]),
  t_("config", "AWS Config", ["config:Describe*", "config:Get*", "config:List*"]),
  t_("sts", "STS (Identity)", ["sts:GetCallerIdentity"]),
  // Compute
  t_("ec2", "EC2", ["ec2:Describe*"]),
  t_("lambda", "Lambda", ["lambda:GetFunction", "lambda:ListFunctions", "lambda:GetPolicy"]),
  t_("ecs", "ECS", ["ecs:Describe*", "ecs:List*"]),
  t_("eks", "EKS", ["eks:Describe*", "eks:List*"]),
  t_("autoscaling", "Auto Scaling", ["autoscaling:Describe*"]),
  // Storage
  t_("s3-readonly", "S3 (Read-Only)", ["s3:GetBucketLocation", "s3:GetBucketTagging", "s3:ListAllMyBuckets", "s3:ListBucket"]),
  t_("efs", "EFS", ["elasticfilesystem:Describe*"]),
  // Database
  t_("rds", "RDS", ["rds:Describe*", "rds:List*"]),
  t_("dynamodb-readonly", "DynamoDB (Read-Only)", ["dynamodb:Describe*", "dynamodb:List*"]),
  t_("elasticache", "ElastiCache", ["elasticache:Describe*", "elasticache:List*"]),
  t_("redshift", "Redshift", ["redshift:Describe*", "redshift:List*"]),
  t_("opensearch", "OpenSearch", ["es:Describe*", "es:List*"]),
  // Networking
  t_("route53", "Route 53", ["route53:Get*", "route53:List*"]),
  t_("elb", "Elastic Load Balancing", ["elasticloadbalancing:Describe*"]),
  t_("api-gateway", "API Gateway", ["apigateway:GET"]),
  t_("cloudfront", "CloudFront", ["cloudfront:Get*", "cloudfront:List*"]),
  // Messaging & integration
  t_("sns", "SNS", ["sns:Get*", "sns:List*"]),
  t_("sqs", "SQS", ["sqs:Get*", "sqs:List*"]),
  t_("eventbridge", "EventBridge", ["events:Describe*", "events:List*"]),
  t_("step-functions", "Step Functions", ["states:Describe*", "states:List*", "states:GetExecutionHistory"]),
  // Management & governance
  t_("cloudformation", "CloudFormation", ["cloudformation:Describe*", "cloudformation:List*", "cloudformation:GetTemplate", "cloudformation:GetTemplateSummary"]),
  t_("ssm", "Systems Manager", ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:DescribeParameters", "ssm:List*"]),
  t_("service-quotas", "Service Quotas", ["servicequotas:Get*", "servicequotas:List*"]),
  t_("health", "AWS Health", ["health:Describe*"]),
  t_("compute-optimizer", "Compute Optimizer", ["compute-optimizer:Get*"]),
  // Cost
  t_("cost-explorer", "Cost Explorer", ["ce:Get*", "ce:Describe*", "ce:List*"]),
  t_("aws-pricing", "Pricing", ["pricing:GetProducts", "pricing:DescribeServices"]),
  // Analytics
  t_("athena", "Athena", ["athena:Get*", "athena:List*", "athena:BatchGet*"]),
  t_("glue", "Glue", ["glue:Get*", "glue:List*", "glue:BatchGet*"]),
  t_("kinesis", "Kinesis", ["kinesis:Describe*", "kinesis:List*", "kinesis:Get*"]),
  // AI/ML
  t_("sagemaker", "SageMaker", ["sagemaker:Describe*", "sagemaker:List*"]),
  t_("bedrock-readonly", "Bedrock (Read-Only)", ["bedrock:Get*", "bedrock:List*"]),
  // Identity
  t_("cognito", "Cognito", ["cognito-idp:Describe*", "cognito-idp:List*"]),
  // Backup & resilience
  t_("backup", "AWS Backup", ["backup:Describe*", "backup:Get*", "backup:List*"]),
  // Architecture
  t_("well-architected", "Well-Architected", ["wellarchitected:Get*", "wellarchitected:List*"]),
  // Containers
  t_("ecr", "ECR", ["ecr:Describe*", "ecr:List*", "ecr:BatchGetImage"]),
];

// ── Permission status per target ──

interface TargetStatus {
  granted: boolean;
  missingActions: string[];
  loading: boolean;
}

export default function IamPermissionsTab() {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  const wsId = currentWorkspace?.workspaceId;

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
    } catch (err) {
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
      // Re-check permissions after role creation
      await checkPermissions();
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("iam.createFailed");
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  };

  const handleGrant = async (targetName: string) => {
    if (!wsId) return;
    setGrantingTargets((prev) => new Set(prev).add(targetName));
    try {
      await grantMcpTargets(wsId, [targetName]);
      toast.success(t("iam.grantSuccess", { target: targetName }));
      // Re-check permissions after grant
      await checkPermissions(true);
    } catch (err) {
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
          {isAdmin ? (
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
        <div className="flex justify-between gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
          <span className="text-gray-500 dark:text-gray-400">{t("iam.permissionBoundary")}</span>
          <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
            <CheckCircle2 className="w-3 h-3" /> AgentStudioWorkspaceCeiling
          </span>
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

            return (
              <div
                key={target.name}
                className="border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden"
              >
                <div className="flex items-center gap-2 px-3 py-2">
                  {/* Status icon */}
                  {isGranted ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />
                  ) : (
                    <Lock className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  )}

                  {/* Name */}
                  <span className="text-xs font-medium text-gray-800 dark:text-gray-200">
                    {target.displayName}
                  </span>

                  {/* Status text */}
                  <span className="flex-1 text-[11px] text-gray-400 dark:text-gray-500 truncate">
                    {noIamNeeded
                      ? t("iam.noExtraPerms")
                      : isGranted
                        ? t("iam.allPermsGranted")
                        : t("iam.missingPerms", { count: missingCount })}
                  </span>

                  {/* Action buttons for denied targets */}
                  {!isGranted && !noIamNeeded && (
                    <>
                      {isAdmin ? (
                        <button
                          onClick={() => handleGrant(target.name)}
                          disabled={isGranting}
                          className="flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded transition-colors disabled:opacity-50"
                        >
                          {isGranting ? (
                            <Loader2 className="w-2.5 h-2.5 animate-spin" />
                          ) : null}
                          {t("iam.autoGrant")}
                        </button>
                      ) : (
                        <span className="text-[10px] text-amber-600 dark:text-amber-400 flex items-center gap-0.5">
                          <Lock className="w-2.5 h-2.5" /> {t("iam.contactAdmin")}
                        </span>
                      )}
                      <button
                        onClick={() => toggleExpanded(target.name)}
                        className="flex items-center gap-0.5 px-2 py-0.5 text-[10px] font-medium text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors"
                      >
                        {t("iam.manual")}
                        {isExpanded ? (
                          <ChevronDown className="w-2.5 h-2.5" />
                        ) : (
                          <ChevronRight className="w-2.5 h-2.5" />
                        )}
                      </button>
                    </>
                  )}
                </div>

                {/* Expanded manual section */}
                {isExpanded && !isGranted && !noIamNeeded && status && (
                  <div className="px-3 pb-3 border-t border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50">
                    <div className="pt-3 space-y-3">
                      {/* Missing actions list */}
                      <div>
                        <p className="text-[10px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide mb-1">
                          {t("iam.missingActions")}
                        </p>
                        <div className="flex flex-wrap gap-1">
                          {status.missingActions.map((action) => (
                            <span
                              key={action}
                              className="px-1.5 py-0.5 text-[10px] font-mono bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded border border-red-200 dark:border-red-800"
                            >
                              {action}
                            </span>
                          ))}
                        </div>
                      </div>

                      {/* CLI command */}
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <p className="text-[10px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">
                            {t("iam.cliCommand")}
                          </p>
                          <button
                            onClick={() => copyToClipboard(buildCliCommand(target), target.name)}
                            className="flex items-center gap-1 text-[10px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                          >
                            {copiedTarget === target.name ? (
                              <><Check className="w-2.5 h-2.5" /> {t("iam.copied")}</>
                            ) : (
                              <><Copy className="w-2.5 h-2.5" /> {t("common.copy")}</>
                            )}
                          </button>
                        </div>
                        <pre className="text-[10px] font-mono bg-gray-900 dark:bg-gray-950 text-green-400 p-3 rounded-lg overflow-x-auto leading-relaxed whitespace-pre-wrap">
                          {buildCliCommand(target)}
                        </pre>
                      </div>

                      {/* Console steps */}
                      <div>
                        <p className="text-[10px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide mb-1">
                          {t("iam.consoleSteps")}
                        </p>
                        <ol className="text-[10px] text-gray-600 dark:text-gray-400 space-y-1 list-decimal list-inside">
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
    </section>
  );
}
