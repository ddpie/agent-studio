import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface WorkspaceBoundaryProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

/**
 * Permission Boundary (managed policy) that caps the maximum permissions
 * any workspace IAM role can ever have.
 *
 * Effective permissions = identity policy ∩ boundary.
 * Actions not Allow-listed in the boundary are implicitly denied.
 * The explicit DenyEscalation statement is belt-and-suspenders defense
 * against future accidental widening of the Allow set.
 *
 * ⚠ Size limit: Managed policy cap is 6,144 characters.
 * ⚠ Updates take effect immediately on all bound workspace roles.
 */
export class WorkspaceBoundary extends Construct {
  /** ARN of the AgentStudioWorkspaceCeiling managed policy. */
  public readonly boundaryPolicyArn: string;

  constructor(scope: Construct, id: string, props: WorkspaceBoundaryProps) {
    super(scope, id);

    const { region, accountId, s3Bucket } = props;

    const boundary = new iam.ManagedPolicy(this, "WorkspaceCeiling", {
      managedPolicyName: "AgentStudioWorkspaceCeiling",
      statements: [
        // ─── Platform baseline ───
        new iam.PolicyStatement({
          sid: "Bedrock",
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
          sid: "AgentCoreRuntime",
          actions: [
            "bedrock-agentcore:InvokeAgentRuntime",
            "bedrock-agentcore:GetAgentRuntime",
            "bedrock-agentcore:ListAgentRuntimes",
          ],
          resources: [
            `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*`,
            `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*/runtime-endpoint/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "AgentCoreServices",
          actions: [
            "bedrock-agentcore:InvokeCodeInterpreter",
            "bedrock-agentcore:StartCodeInterpreterSession",
            "bedrock-agentcore:StopCodeInterpreterSession",
            "bedrock-agentcore:InvokeBrowser",
            "bedrock-agentcore:StartBrowserSession",
            "bedrock-agentcore:StopBrowserSession",
            "bedrock-agentcore:GetBrowserSession",
            "bedrock-agentcore:ListBrowserSessions",
            "bedrock-agentcore:SaveBrowserSessionProfile",
            "bedrock-agentcore:UpdateBrowserStream",
            "bedrock-agentcore:ConnectBrowserStream",
            "bedrock-agentcore:ConnectBrowserAutomationStream",
          ],
          resources: [
            `arn:aws:bedrock-agentcore:${region}:${accountId}:code-interpreter-custom/*`,
            `arn:aws:bedrock-agentcore:${region}:${accountId}:browser-custom/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "S3Platform",
          actions: ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:DeleteObject"],
          resources: [
            `arn:aws:s3:::${s3Bucket}`,
            `arn:aws:s3:::${s3Bucket}/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "DynamoDBPlatform",
          actions: [
            "dynamodb:GetItem",
            "dynamodb:Query",
            "dynamodb:Scan",
            "dynamodb:BatchGetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
          ],
          resources: [
            `arn:aws:dynamodb:${region}:${accountId}:table/agent-studio-*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "SecretsRead",
          actions: ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
          resources: [
            `arn:aws:secretsmanager:${region}:${accountId}:secret:agent-studio/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "Observability",
          actions: [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
            "logs:DescribeLogGroups",
            "logs:DescribeLogStreams",
          ],
          resources: [
            `arn:aws:logs:${region}:${accountId}:log-group:/aws/bedrock-agentcore/*`,
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
          sid: "CloudWatchMetrics",
          actions: ["cloudwatch:PutMetricData"],
          resources: ["*"],
          conditions: {
            StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" },
          },
        }),
        new iam.PolicyStatement({
          sid: "GatewayReadOnly",
          actions: [
            "bedrock-agentcore:ListGateways",
            "bedrock-agentcore:GetGateway",
            "bedrock-agentcore:ListGatewayTargets",
            "bedrock-agentcore:GetGatewayTarget",
          ],
          resources: ["*"],
        }),
        new iam.PolicyStatement({
          sid: "ECR",
          actions: [
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
            "ecr:GetAuthorizationToken",
          ],
          resources: ["*"],
        }),

        // ─── Extensible ceiling: read-only for MCP-backed AWS services ───
        // Only services with actual MCP targets. Add more as targets onboard.
        new iam.PolicyStatement({
          sid: "AWSServicesReadCeiling",
          actions: [
            "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
            "logs:Describe*", "logs:Get*", "logs:StartQuery", "logs:StopQuery", "logs:FilterLogEvents",
            "cloudtrail:LookupEvents", "cloudtrail:Get*", "cloudtrail:List*", "cloudtrail:StartQuery",
            "iam:GetUser", "iam:GetRole", "iam:GetPolicy", "iam:GetPolicyVersion",
            "iam:ListRoles", "iam:ListPolicies", "iam:ListAttachedRolePolicies",
            "ec2:Describe*",
            "lambda:GetFunction", "lambda:ListFunctions",
            "ecs:Describe*", "ecs:List*",
            "eks:Describe*", "eks:List*",
            "dynamodb:Describe*", "dynamodb:List*", "dynamodb:Query", "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:BatchGetItem",
            "sns:Get*", "sns:List*",
            "sqs:Get*", "sqs:List*",
            "states:Describe*", "states:List*",
            "pricing:GetProducts", "pricing:DescribeServices",
            "wellarchitected:Get*", "wellarchitected:List*",
            "support:DescribeTrustedAdvisor*",
            "bedrock:InvokeModel",
          ],
          resources: ["*"],
        }),

        // ─── Belt-and-suspenders deny (compact wildcards) ───
        new iam.PolicyStatement({
          sid: "DenyEscalation",
          effect: iam.Effect.DENY,
          actions: [
            "iam:Create*", "iam:Delete*", "iam:Put*", "iam:Attach*",
            "iam:Detach*", "iam:Update*", "iam:Add*", "iam:PassRole",
            "sts:AssumeRole*", "sts:GetFederationToken", "sts:GetSessionToken",
            "organizations:*", "account:*",
          ],
          resources: ["*"],
        }),
      ],
    });

    this.boundaryPolicyArn = boundary.managedPolicyArn;

    new cdk.CfnOutput(this, "WorkspaceCeilingArn", {
      value: boundary.managedPolicyArn,
      exportName: "AgentStudio-WorkspaceCeilingArn",
    });
  }
}
