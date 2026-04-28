import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface InvokeProps {
  config: AgentStudioConfig;
  cognitoUserPoolId: string;
  cognitoClientId: string;
  metaAgentArn: string;
  workspacesTable: dynamodb.Table;
  agentsTable: dynamodb.ITable;
}

export class Invoke extends Construct {
  public readonly invokeLambda: lambda.Function;
  public readonly functionUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: InvokeProps) {
    super(scope, id);

    this.invokeLambda = new lambda.Function(this, "InvokeHandler", {
      functionName: "agent-studio-invoke",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/invoke-node"), {
        assetHashType: cdk.AssetHashType.SOURCE,
        bundling: {
          image: lambda.Runtime.NODEJS_22_X.bundlingImage,
          command: ["bash", "-c",
            "cp package.json package-lock.json /asset-output/ && " +
            "cp *.mjs /asset-output/ && " +
            "cd /asset-output && HOME=/tmp npm ci --omit=dev"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(300),
      memorySize: 1024,
      environment: {
        ACCOUNT_ID: props.config.accountId,
        WORKSPACES_TABLE: props.workspacesTable.tableName,
        AGENTS_TABLE: props.agentsTable.tableName,
        COGNITO_USER_POOL_ID: props.cognitoUserPoolId,
        COGNITO_CLIENT_ID: props.cognitoClientId,
        META_AGENT_ARN: props.metaAgentArn,
      },
    });

    // Function URL with IAM auth (CloudFront OAC signs requests)
    this.functionUrl = this.invokeLambda.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    // Grant DDB read for membership check
    props.workspacesTable.grantReadData(this.invokeLambda);

    // Imported table: explicit IAM policy (grantReadData on ITable misses GSI ARN)
    this.invokeLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["dynamodb:GetItem", "dynamodb:Query"],
      resources: [
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents`,
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents/index/*`,
      ],
    }));

    // Grant AgentCore invoke
    this.invokeLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [`arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*`],
    }));

    // Grant S3 read for presigned URLs (attachments)
    this.invokeLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["s3:GetObject"],
      resources: [`arn:aws:s3:::${props.config.s3Bucket}/uploads/*`],
    }));

    // Per-workspace Kiro API key lookup. Read-only, prefix-scoped so the
    // Invoke Lambda can't reach agent-level secrets (those belong to
    // Meta-Agent / Sub-Agent roles). The trailing `??????` is the 6-char
    // Secrets Manager suffix — using `kiro-api-key-*` instead would also
    // match hypothetical `kiro-api-key-legacy-*` names.
    this.invokeLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["secretsmanager:GetSecretValue"],
      resources: [
        `arn:aws:secretsmanager:${props.config.region}:${props.config.accountId}:secret:agent-studio/workspaces/*/kiro-api-key-??????`,
      ],
    }));

    new cdk.CfnOutput(this, "InvokeFunctionUrl", { value: this.functionUrl.url });
  }
}
