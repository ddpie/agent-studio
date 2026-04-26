import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";
import { Construct } from "constructs";

export interface McpRolesProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

/**
 * Registry target entry as declared in mcp-registry.yaml.
 * Only the fields this construct cares about.
 */
interface RegistryTarget {
  name: string;
  enabled?: boolean;
  vpc_required?: boolean;
  deprecated?: boolean;
  iam_policy?: {
    Statement: Array<{
      Effect: string;
      Action: string | string[];
      Resource: string | string[];
    }>;
  } | null;
}

interface Registry {
  remote_targets?: RegistryTarget[];
  runtime_targets?: RegistryTarget[];
}

/**
 * Per-target MCP IAM roles.
 *
 * Reads `mcp-runtime/mcp-registry.yaml` at CDK synth time and creates a
 * dedicated IAM role for each enabled target that declares an `iam_policy`.
 * Targets without `iam_policy` (or with `iam_policy: null`) continue to
 * use the shared `AgentStudioSubAgent-basic-{region}` role.
 *
 * These roles are NOT gated by the WorkspaceCeiling permission boundary
 * because MCP servers may need write operations that the boundary blocks.
 * Security is ensured by: (a) CDK-managed, users cannot modify;
 * (b) inline policy scoped to declared actions; (c) trust policy limited
 * to bedrock-agentcore.amazonaws.com + aws:SourceAccount.
 */
export class McpRoles extends Construct {
  /** Map of target name -> IAM role ARN for targets with custom roles. */
  public readonly roleMapping: Record<string, string>;

  constructor(scope: Construct, id: string, props: McpRolesProps) {
    super(scope, id);

    const { region, accountId, s3Bucket } = props;

    // ─── Read registry at synth time ───
    const registryPath = path.resolve(__dirname, "../../../mcp-runtime/mcp-registry.yaml");
    const registryContent = fs.readFileSync(registryPath, "utf-8");
    const registry = yaml.load(registryContent) as Registry;

    // ─── Collect enabled targets with iam_policy ───
    const targets: RegistryTarget[] = [];
    for (const t of registry.runtime_targets ?? []) {
      if (!t.enabled || t.vpc_required || t.deprecated) continue;
      if (t.iam_policy && t.iam_policy.Statement?.length) {
        targets.push(t);
      }
    }
    for (const t of registry.remote_targets ?? []) {
      if (!t.enabled) continue;
      if (t.iam_policy && t.iam_policy.Statement?.length) {
        targets.push(t);
      }
    }

    // ─── Trust principal (shared across all MCP roles) ───
    const trustPrincipal = new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
      conditions: {
        StringEquals: { "aws:SourceAccount": accountId },
      },
    });

    // ─── Platform baseline statements every AgentCore runtime needs ───
    const baselineStatements = [
      new iam.PolicyStatement({
        sid: "Logs",
        actions: [
          "logs:CreateLogGroup",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ],
        resources: [
          `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        ],
      }),
      new iam.PolicyStatement({
        sid: "XRay",
        actions: [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        sid: "ECR",
        actions: ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
        resources: [`arn:aws:ecr:${region}:${accountId}:repository/*`],
      }),
      new iam.PolicyStatement({
        sid: "ECRAuth",
        actions: ["ecr:GetAuthorizationToken"],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        sid: "CloudWatchMetrics",
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"],
        conditions: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } },
      }),
      new iam.PolicyStatement({
        sid: "BedrockInvoke",
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          `arn:aws:bedrock:*:${accountId}:inference-profile/*`,
        ],
      }),
      new iam.PolicyStatement({
        sid: "S3PlatformRead",
        actions: ["s3:GetObject", "s3:ListBucket"],
        resources: [
          `arn:aws:s3:::${s3Bucket}`,
          `arn:aws:s3:::${s3Bucket}/*`,
        ],
      }),
    ];

    // ─── Create per-target roles ───
    this.roleMapping = {};

    for (const target of targets) {
      const roleName = `AgentStudioMCP-${target.name}-${region}`;

      // Sanity check: IAM role names must be <= 64 characters
      if (roleName.length > 64) {
        throw new Error(
          `MCP role name exceeds 64-char IAM limit: "${roleName}" (${roleName.length} chars). ` +
          `Shorten the target name "${target.name}" in mcp-registry.yaml.`
        );
      }

      // Use a CFN-safe logical ID (replace hyphens since CDK appends hash)
      const logicalId = `McpRole${target.name.replace(/-/g, "")}`;

      const role = new iam.Role(this, logicalId, {
        roleName,
        assumedBy: trustPrincipal,
      });

      // Attach platform baseline
      for (const stmt of baselineStatements) {
        role.addToPolicy(stmt);
      }

      // Attach target-specific inline policy from registry iam_policy
      const targetStatements = target.iam_policy!.Statement;
      for (const stmt of targetStatements) {
        const actions = Array.isArray(stmt.Action) ? stmt.Action : [stmt.Action];
        const resources = Array.isArray(stmt.Resource) ? stmt.Resource : [stmt.Resource];
        role.addToPolicy(new iam.PolicyStatement({
          effect: stmt.Effect === "Deny" ? iam.Effect.DENY : iam.Effect.ALLOW,
          actions,
          resources,
        }));
      }

      this.roleMapping[target.name] = role.roleArn;

      // Export individual role ARN for deploy-mcp.sh to consume
      new cdk.CfnOutput(this, `McpRoleArn${target.name.replace(/-/g, "")}`, {
        value: role.roleArn,
        exportName: `AgentStudio-McpRoleArn-${target.name}`,
      });
    }

    // ─── Export full mapping as JSON CfnOutput ───
    new cdk.CfnOutput(this, "McpRoleMapping", {
      value: JSON.stringify(this.roleMapping),
      description: "JSON mapping of MCP target name to per-target IAM role ARN",
    });
  }
}
