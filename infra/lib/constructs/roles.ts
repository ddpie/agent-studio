import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface AgentCoreRolesProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

export class AgentCoreRoles extends Construct {
  public readonly subAgentRoleArn: string;
  public readonly metaAgentRoleArn: string;
  public readonly evaluatorRoleArn: string;
  public readonly schedulerTargetRoleArn: string;

  // Legacy aliases for existing stack references
  public readonly basicRoleArn: string;
  public readonly readonlyRoleArn: string;
  public readonly dataAccessRoleArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreRolesProps) {
    super(scope, id);

    const trustPrincipal = new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
      conditions: {
        StringEquals: { "aws:SourceAccount": props.accountId },
      },
    });

    // ─── Baseline: runtime infrastructure (all roles need these) ───
    const baselineStatements = [
      new iam.PolicyStatement({
        actions: ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
        resources: [`arn:aws:ecr:${props.region}:${props.accountId}:repository/*`],
      }),
      new iam.PolicyStatement({
        actions: ["ecr:GetAuthorizationToken"],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        actions: ["logs:CreateLogGroup", "logs:DescribeLogGroups", "logs:DescribeLogStreams", "logs:CreateLogStream", "logs:PutLogEvents"],
        resources: [`arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`],
      }),
      new iam.PolicyStatement({
        actions: ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"],
        conditions: { StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" } },
      }),
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"],
        resources: [`arn:aws:bedrock:${props.region}::foundation-model/*`],
      }),
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:GetAgentRuntime",
        ],
        resources: [`arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`],
      }),
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:StartBrowserSession",
          "bedrock-agentcore:InvokeBrowser",
          "bedrock-agentcore:StopBrowserSession",
          "bedrock-agentcore:GetBrowserSession",
          "bedrock-agentcore:UpdateBrowserStream",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:code-interpreter-custom/*`,
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:browser-custom/*`,
        ],
      }),
    ];

    // ─── Sub-Agent data statements (shared by all sub-agent roles) ───
    const subAgentDataStatements = [
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`arn:aws:s3:::${props.s3Bucket}/*`],
      }),
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [`arn:aws:s3:::${props.s3Bucket}`],
      }),
      new iam.PolicyStatement({
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [
          `arn:aws:s3:::${props.s3Bucket}/runs/*`,
          `arn:aws:s3:::${props.s3Bucket}/outputs/*`,
          `arn:aws:s3:::${props.s3Bucket}/uploads/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"],
        resources: [
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs/index/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ["dynamodb:PutItem", "dynamodb:UpdateItem"],
        resources: [`arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`],
      }),
    ];

    // Helper to apply all sub-agent policies to a role
    const applySubAgentPolicies = (role: iam.Role) => {
      baselineStatements.forEach((s) => role.addToPolicy(s));
      subAgentDataStatements.forEach((s) => role.addToPolicy(s));
    };

    // ─── Sub-Agent Role (primary, logical ID "BasicRole" preserved for CFN compat) ───
    const subAgentRole = new iam.Role(this, "BasicRole", {
      roleName: `AgentStudioSubAgent-basic-${props.region}`,
      assumedBy: trustPrincipal,
    });
    applySubAgentPolicies(subAgentRole);

    // Legacy roles — already-deployed agents reference these ARNs.
    // Same policies as subAgentRole so all tiers behave identically.
    const readonlyRole = new iam.Role(this, "ReadonlyRole", {
      roleName: `AgentStudioSubAgentRole-${props.region}`,
      assumedBy: trustPrincipal,
    });
    applySubAgentPolicies(readonlyRole);

    const dataAccessRole = new iam.Role(this, "DataAccessRole", {
      roleName: `AgentStudioSubAgent-dataaccess-${props.region}`,
      assumedBy: trustPrincipal,
    });
    applySubAgentPolicies(dataAccessRole);

    // ─── Meta-Agent Role: manages agent lifecycle ───
    const metaAgentRole = new iam.Role(this, "MetaAgentRole", {
      roleName: `AgentStudioMetaAgent-${props.region}`,
      assumedBy: trustPrincipal,
    });
    baselineStatements.forEach((s) => metaAgentRole.addToPolicy(s));
    // S3 full read + write to entire bucket
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      resources: [`arn:aws:s3:::${props.s3Bucket}/*`],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3:ListBucket"],
      resources: [`arn:aws:s3:::${props.s3Bucket}`],
    }));
    // DDB full CRUD on platform tables (except a2a-keys)
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"],
      resources: [
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs/index/*`,
      ],
    }));
    // AgentCore control-plane
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:CreateAgentRuntime",
        "bedrock-agentcore:UpdateAgentRuntime",
        "bedrock-agentcore:DeleteAgentRuntime",
      ],
      resources: [`arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`],
    }));

    this.subAgentRoleArn = subAgentRole.roleArn;
    this.metaAgentRoleArn = metaAgentRole.roleArn;
    this.basicRoleArn = subAgentRole.roleArn;
    this.readonlyRoleArn = readonlyRole.roleArn;
    this.dataAccessRoleArn = dataAccessRole.roleArn;

    new cdk.CfnOutput(this, "SubAgentRoleArn", { value: subAgentRole.roleArn });
    new cdk.CfnOutput(this, "MetaAgentRoleArn", { value: metaAgentRole.roleArn });
    new cdk.CfnOutput(this, "BasicRoleArn", { value: subAgentRole.roleArn });
    new cdk.CfnOutput(this, "ReadonlyRoleArn", { value: readonlyRole.roleArn });
    new cdk.CfnOutput(this, "DataAccessRoleArn", { value: dataAccessRole.roleArn });

    // ─── Evaluator execution role ───
    const evaluatorRole = new iam.Role(this, "EvaluatorExecutionRole", {
      roleName: `AgentStudioEvaluatorExecution-${props.region}`,
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": props.accountId },
        },
      }),
    });
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:FilterLogEvents",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ],
      resources: [
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:aws/spans`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:aws/spans:*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/evaluations/*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/vendedlogs/bedrock-agentcore/evaluation/*`,
      ],
    }));
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: ["logs:DescribeLogGroups"],
      resources: ["*"],
    }));
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"],
      resources: [`arn:aws:bedrock:${props.region}::foundation-model/*`],
    }));
    this.evaluatorRoleArn = evaluatorRole.roleArn;

    new cdk.CfnOutput(this, "EvaluatorRoleArn", {
      value: evaluatorRole.roleArn,
      exportName: "AgentStudio-EvaluatorRoleArn",
    });

    // ─── Scheduler target role ───
    const schedulerTargetRole = new iam.Role(this, "SchedulerTargetRole", {
      roleName: `AgentStudioSchedulerTargetRole-${props.region}`,
      assumedBy: new iam.ServicePrincipal("scheduler.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": props.accountId },
        },
      }),
    });
    schedulerTargetRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`,
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*/runtime-endpoint/*`,
      ],
    }));
    this.schedulerTargetRoleArn = schedulerTargetRole.roleArn;

    new cdk.CfnOutput(this, "SchedulerTargetRoleArn", {
      value: schedulerTargetRole.roleArn,
      exportName: "AgentStudio-SchedulerTargetRoleArn",
    });
  }
}
