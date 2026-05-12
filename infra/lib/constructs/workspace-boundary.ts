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
          sid: "ACRuntime",
          actions: ["bedrock-agentcore:InvokeAgentRuntime"],
          resources: [
            `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*`,
            `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*/runtime-endpoint/*`,
          ],
        }),
        new iam.PolicyStatement({
          // Tools + memory data plane. Invoke* / Save* / Update* / Connect*
          // serve browser + code-interpreter; CreateEvent + Retrieve* serve
          // harness memory recall + writeback. All three resource patterns
          // are workspace-scoped via wildcards on the shared namespaces.
          sid: "ACSvc",
          actions: [
            "bedrock-agentcore:Invoke*",
            "bedrock-agentcore:Start*",
            "bedrock-agentcore:Stop*",
            "bedrock-agentcore:Save*",
            "bedrock-agentcore:Update*",
            "bedrock-agentcore:Connect*",
            "bedrock-agentcore:CreateEvent",
            "bedrock-agentcore:Retrieve*",
          ],
          resources: [
            `arn:aws:bedrock-agentcore:${region}:${accountId}:code-interpreter-custom/*`,
            `arn:aws:bedrock-agentcore:${region}:${accountId}:browser-custom/*`,
            `arn:aws:bedrock-agentcore:${region}:${accountId}:memory/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "S3Plat",
          actions: ["s3:GetObject", "s3:ListBucket", "s3:PutObject", "s3:DeleteObject"],
          resources: [
            `arn:aws:s3:::${s3Bucket}`,
            `arn:aws:s3:::${s3Bucket}/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "DDBPlat",
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
          sid: "SecRead",
          actions: ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
          resources: [
            `arn:aws:secretsmanager:${region}:${accountId}:secret:agent-studio/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: "Obs",
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
          sid: "CWMetric",
          actions: ["cloudwatch:PutMetricData"],
          resources: ["*"],
          conditions: {
            StringEquals: { "cloudwatch:namespace": "bedrock-agentcore" },
          },
        }),
        new iam.PolicyStatement({
          sid: "ECR",
          actions: [
            "ecr:GetDownloadUrlForLayer",
            "ecr:GetAuthorizationToken",
            "ecr:BatchGetImage",
            // Harness images live on ECR Public.
            "ecr-public:GetAuthorizationToken",
            "ecr-public:BatchGetImage",
            "ecr-public:GetDownloadUrlForLayer",
          ],
          resources: ["*"],
        }),
        new iam.PolicyStatement({
          sid: "HBearer",
          actions: ["sts:GetServiceBearerToken"],
          resources: ["*"],
          conditions: {
            StringEquals: { "sts:AWSServiceName": "bedrock-agentcore.amazonaws.com" },
          },
        }),

        // ─── Extensible ceiling: read-only access for AWS services ───
        // Boundary ceiling — not a grant. Keep under 6,144 chars after synthesis.
        // Removed: cloudformation:GetTemplate (leaks secrets in templates),
        // ssm:Get* scoped to DescribeParameters + GetParameter (not GetParametersByPath).
        // Trimmed: lightsail, fsx, memorydb, synthetics, resiliencehub, savingsplans,
        // cost-optimization-hub, apprunner, batch (re-add when MCP targets exist).
        new iam.PolicyStatement({
          sid: "AWSReadCeil",
          actions: [
            // Compute / containers
            "ec2:Describe*",
            "lambda:Get*", "lambda:List*",
            "ecs:Describe*", "ecs:List*",
            "eks:Describe*", "eks:List*",
            "autoscaling:Describe*",
            "ecr:Describe*", "ecr:List*", "ecr:BatchGetImage",
            "compute-optimizer:Get*",
            // Storage
            "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:ListAllMyBuckets", "s3:ListBucket",
            "s3:GetObject",
            "s3tables:Get*", "s3tables:List*",
            "elasticfilesystem:Describe*",
            // Database
            "rds:Describe*", "rds:List*",
            "dynamodb:Describe*", "dynamodb:List*",
            "dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:Query", "dynamodb:Scan",
            "elasticache:Describe*", "elasticache:List*",
            "redshift:Describe*", "redshift:List*",
            "redshift-data:Describe*", "redshift-data:Get*",
            "es:Describe*", "es:List*",
            "neptune-db:ReadDataViaQuery",
            "timestream:Describe*", "timestream:List*", "timestream:SelectValues",
            // Analytics
            "athena:Get*", "athena:List*", "athena:BatchGet*",
            "glue:Get*", "glue:List*", "glue:BatchGet*",
            "kinesis:Describe*", "kinesis:List*", "kinesis:Get*",
            "firehose:Describe*", "firehose:List*",
            // Monitoring / observability
            "cloudwatch:Describe*", "cloudwatch:Get*", "cloudwatch:List*",
            "logs:Describe*", "logs:Get*", "logs:List*",
            "logs:*Query", "logs:Filter*",
            "cloudtrail:Describe*", "cloudtrail:Get*", "cloudtrail:List*",
            "cloudtrail:Lookup*", "cloudtrail:*Query",
            "application-signals:Get*", "application-signals:List*",
            "aps:Describe*", "aps:Get*", "aps:List*", "aps:QueryMetrics",
            "pi:Describe*", "pi:Get*", "pi:List*",
            // Security / identity
            "iam:Get*", "iam:List*", "iam:Simulate*",
            "kms:Describe*", "kms:List*", "kms:Get*",
            "acm:Describe*", "acm:List*", "acm:GetCertificate",
            "secretsmanager:Describe*", "secretsmanager:List*",
            "guardduty:Get*", "guardduty:List*",
            "securityhub:Get*", "securityhub:List*", "securityhub:BatchGet*",
            "inspector2:Get*", "inspector2:List*", "inspector2:BatchGet*",
            "config:Describe*", "config:Get*", "config:List*",
            // Networking
            "route53:Get*", "route53:List*",
            "elasticloadbalancing:Describe*",
            "apigateway:GET",
            "cloudfront:Get*", "cloudfront:List*",
            "appsync:Get*", "appsync:List*",
            // Messaging
            "sns:Get*", "sns:List*",
            "sqs:Get*", "sqs:List*",
            "events:Describe*", "events:List*",
            "states:Describe*", "states:List*",
            "mq:Describe*", "mq:List*",
            "kafka:Describe*", "kafka:Get*", "kafka:List*",
            // Management
            "cloudformation:Describe*", "cloudformation:List*", "cloudformation:GetTemplateSummary",
            "ssm:DescribeParameters", "ssm:GetParameter", "ssm:GetParameters", "ssm:List*",
            "servicequotas:Get*", "servicequotas:List*",
            "tag:Get*", "health:Describe*",
            "support:Describe*",
            // Cost
            "ce:Describe*", "ce:Get*", "ce:List*",
            "pricing:GetProducts", "pricing:DescribeServices",
            "budgets:Describe*", "budgets:View*",
            // AI/ML — bedrock:Retrieve is read-only KB retrieval (billable, not a write)
            "bedrock:Get*", "bedrock:List*", "bedrock:Retrieve*",
            "bedrock-agent-runtime:Retrieve",
            "bedrock-agentcore:Get*", "bedrock-agentcore:List*",
            "sagemaker:Describe*", "sagemaker:List*",
            "kendra:Describe*", "kendra:List*", "kendra:Query", "kendra:Retrieve",
            "qbusiness:ChatSync", "qbusiness:Get*", "qbusiness:List*",
            // Industry
            "geo:Get*", "geo:List*", "geo:Search*", "geo:CalculateRoute",
            "healthlake:Describe*", "healthlake:List*",
            "healthlake:Read*", "healthlake:Search*",
            "iotsitewise:Describe*", "iotsitewise:Get*", "iotsitewise:List*", "iotsitewise:BatchGet*",
            "omics:Get*", "omics:List*",
            // Other
            "wellarchitected:Get*", "wellarchitected:List*",
            "sts:GetCallerIdentity",
            "cognito-idp:Describe*", "cognito-idp:List*",
            "backup:Describe*", "backup:Get*", "backup:List*",
          ],
          resources: ["*"],
        }),

        // ─── Belt-and-suspenders deny (compact wildcards) ───
        new iam.PolicyStatement({
          sid: "DenyEsc",
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
