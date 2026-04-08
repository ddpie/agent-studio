import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface InvokeProps {
  config: AgentStudioConfig;
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
            "cp package.json /asset-output/ && " +
            "cp handler.mjs /asset-output/ && " +
            "cd /asset-output && HOME=/tmp npm install --omit=dev"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(300),
      memorySize: 1024,
      environment: {
        ACCOUNT_ID: props.config.accountId,
        WORKSPACES_TABLE: props.workspacesTable.tableName,
        AGENTS_TABLE: props.agentsTable.tableName,
        COGNITO_USER_POOL_ID: props.config.cognitoUserPoolId,
        COGNITO_CLIENT_ID: props.config.cognitoClientId,
        META_AGENT_ARN: `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/${props.config.metaAgentId}`,
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

    new cdk.CfnOutput(this, "InvokeFunctionUrl", { value: this.functionUrl.url });
  }
}
