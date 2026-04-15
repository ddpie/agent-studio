import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface AgentCoreRolesProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

export class AgentCoreRoles extends Construct {
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

    // Baseline permissions shared by all tiers
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
      // Allow sub-agents to discover and invoke MCP Runtimes directly
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:GetAgentRuntime",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/mcp_*`,
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`,
        ],
      }),
    ];

    const s3ReadStatements = [
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [
          `arn:aws:s3:::${props.s3Bucket}/agents/*`,
          `arn:aws:s3:::${props.s3Bucket}/skills/*`,
          `arn:aws:s3:::${props.s3Bucket}/uploads/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [`arn:aws:s3:::${props.s3Bucket}`],
        conditions: { StringLike: { "s3:prefix": ["agents/*", "skills/*", "uploads/*"] } },
      }),
    ];

    const ddbReadStatements = [
      new iam.PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"],
        resources: [
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces/index/*`,
        ],
      }),
    ];

    const s3WriteStatements = [
      new iam.PolicyStatement({
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [
          `arn:aws:s3:::${props.s3Bucket}/agents/*`,
          `arn:aws:s3:::${props.s3Bucket}/skills/*`,
          `arn:aws:s3:::${props.s3Bucket}/uploads/*`,
          `arn:aws:s3:::${props.s3Bucket}/tools/*`,
        ],
      }),
    ];

    const ddbWriteStatements = [
      new iam.PolicyStatement({
        actions: ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"],
        resources: [
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
        ],
      }),
    ];

    // Role names must match meta-agent/config.py PERMISSION_TIER_ROLES
    const basicRole = new iam.Role(this, "BasicRole", {
      roleName: `AgentStudioSubAgent-basic-${props.region}`,
      assumedBy: trustPrincipal,
    });
    baselineStatements.forEach((s) => basicRole.addToPolicy(s));

    const readonlyRole = new iam.Role(this, "ReadonlyRole", {
      roleName: `AgentStudioSubAgentRole-${props.region}`,
      assumedBy: trustPrincipal,
    });
    [...baselineStatements, ...s3ReadStatements, ...ddbReadStatements].forEach((s) => readonlyRole.addToPolicy(s));

    const dataAccessRole = new iam.Role(this, "DataAccessRole", {
      roleName: `AgentStudioSubAgent-dataaccess-${props.region}`,
      assumedBy: trustPrincipal,
    });
    [...baselineStatements, ...s3ReadStatements, ...ddbReadStatements, ...s3WriteStatements, ...ddbWriteStatements]
      .forEach((s) => dataAccessRole.addToPolicy(s));

    this.basicRoleArn = basicRole.roleArn;
    this.readonlyRoleArn = readonlyRole.roleArn;
    this.dataAccessRoleArn = dataAccessRole.roleArn;

    new cdk.CfnOutput(this, "BasicRoleArn", { value: basicRole.roleArn });
    new cdk.CfnOutput(this, "ReadonlyRoleArn", { value: readonlyRole.roleArn });
    new cdk.CfnOutput(this, "DataAccessRoleArn", { value: dataAccessRole.roleArn });
  }
}
