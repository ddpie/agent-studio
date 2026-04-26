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

        // ─── Extensible ceiling: read-only access for AWS services ───
        // Permission boundary ceiling — not a grant. Workspace roles still need
        // an explicit inline policy. Manually maintained per MCP target onboarding.
        new iam.PolicyStatement({
          sid: "AWSServicesReadCeiling",
          actions: [
            // Compute
            "ec2:Describe*", "ec2:Get*", "ec2:List*",
            "lambda:Get*", "lambda:List*",
            "ecs:Describe*", "ecs:List*",
            "eks:Describe*", "eks:List*",
            "apprunner:Describe*", "apprunner:List*",
            "lightsail:Get*",
            "autoscaling:Describe*",
            "elasticbeanstalk:Describe*", "elasticbeanstalk:List*",
            "batch:Describe*", "batch:List*",
            // Containers & images
            "ecr:Describe*", "ecr:List*", "ecr:BatchGetImage",
            // Storage
            "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:ListAllMyBuckets", "s3:ListBucket",
            "elasticfilesystem:Describe*",
            "fsx:Describe*", "fsx:List*",
            // Database
            "rds:Describe*", "rds:List*",
            "dynamodb:Describe*", "dynamodb:List*",
            "elasticache:Describe*", "elasticache:List*",
            "memorydb:Describe*", "memorydb:List*",
            "redshift:Describe*", "redshift:List*",
            "es:Describe*", "es:List*",
            // Analytics
            "athena:Get*", "athena:List*", "athena:BatchGet*",
            "glue:Get*", "glue:List*", "glue:BatchGet*",
            "kinesis:Describe*", "kinesis:List*", "kinesis:Get*",
            "firehose:Describe*", "firehose:List*",
            "elasticmapreduce:Describe*", "elasticmapreduce:List*",
            // Monitoring & observability
            "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
            "logs:Describe*", "logs:Get*", "logs:StartQuery", "logs:StopQuery", "logs:FilterLogEvents",
            "cloudtrail:LookupEvents", "cloudtrail:Get*", "cloudtrail:List*", "cloudtrail:StartQuery",
            "xray:Get*", "xray:List*", "xray:BatchGet*",
            "application-autoscaling:Describe*",
            "pi:Get*", "pi:Describe*", "pi:List*",
            "synthetics:Describe*", "synthetics:Get*", "synthetics:List*",
            // Security & identity
            "iam:Get*", "iam:List*", "iam:Simulate*",
            "kms:Describe*", "kms:List*", "kms:GetKeyPolicy", "kms:GetKeyRotationStatus",
            "acm:Describe*", "acm:List*", "acm:GetCertificate",
            "secretsmanager:Describe*", "secretsmanager:List*", "secretsmanager:GetResourcePolicy",
            "guardduty:Get*", "guardduty:List*",
            "securityhub:Get*", "securityhub:List*", "securityhub:BatchGet*",
            "inspector2:Get*", "inspector2:List*", "inspector2:BatchGet*",
            "access-analyzer:Get*", "access-analyzer:List*",
            "config:Describe*", "config:Get*", "config:List*",
            // Networking
            "route53:Get*", "route53:List*",
            "route53resolver:Get*", "route53resolver:List*",
            "elasticloadbalancing:Describe*",
            "apigateway:GET",
            "cloudfront:Get*", "cloudfront:List*",
            // Messaging & integration
            "sns:Get*", "sns:List*",
            "sqs:Get*", "sqs:List*",
            "events:Describe*", "events:List*",
            "states:Describe*", "states:List*", "states:GetExecutionHistory",
            "scheduler:Get*", "scheduler:List*",
            // Management & governance
            "cloudformation:Describe*", "cloudformation:List*", "cloudformation:GetTemplate", "cloudformation:GetTemplateSummary",
            "ssm:Describe*", "ssm:Get*", "ssm:List*",
            "servicequotas:Get*", "servicequotas:List*",
            "resource-groups:Get*", "resource-groups:List*",
            "tag:Get*",
            "health:Describe*",
            "support:DescribeTrustedAdvisor*",
            "compute-optimizer:Get*",
            "resiliencehub:Describe*", "resiliencehub:List*",
            // Cost
            "ce:Get*", "ce:Describe*", "ce:List*",
            "pricing:GetProducts", "pricing:DescribeServices",
            "budgets:Describe*", "budgets:View*",
            "savingsplans:Describe*", "savingsplans:List*",
            "cost-optimization-hub:Get*", "cost-optimization-hub:List*",
            // AI/ML
            "bedrock:Get*", "bedrock:List*",
            "sagemaker:Describe*", "sagemaker:List*",
            // Architecture
            "wellarchitected:Get*", "wellarchitected:List*",
            // Identity
            "sts:GetCallerIdentity",
            "cognito-idp:Describe*", "cognito-idp:List*",
            // Backup
            "backup:Describe*", "backup:Get*", "backup:List*",
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
