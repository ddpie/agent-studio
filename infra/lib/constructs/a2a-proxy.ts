import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface A2aProxyProps {
  config: AgentStudioConfig;
  metaAgentArn: string;
  agentsTable: dynamodb.ITable;
  a2aKeysTable: dynamodb.Table;
  publicHost?: string;
}

export class A2aProxy extends Construct {
  public readonly lambda: lambda.Function;
  public readonly functionUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: A2aProxyProps) {
    super(scope, id);

    this.lambda = new lambda.Function(this, "Handler", {
      functionName: "agent-studio-a2a-proxy",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/a2a-proxy"), {
        assetHashType: cdk.AssetHashType.SOURCE,
        bundling: {
          image: lambda.Runtime.NODEJS_22_X.bundlingImage,
          command: ["bash", "-c",
            "cp package.json /asset-output/ && " +
            "cp handler.mjs /asset-output/ && " +
            "cp -r lib /asset-output/ && " +
            "cd /asset-output && HOME=/tmp npm install --omit=dev"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(300),
      memorySize: 512,
      environment: {
        ACCOUNT_ID: props.config.accountId,
        META_AGENT_ARN: props.metaAgentArn,
        AGENTS_TABLE: props.agentsTable.tableName,
        A2A_KEYS_TABLE: props.a2aKeysTable.tableName,
        PUBLIC_HOST: props.publicHost || "",
      },
    });

    // SigV4 invoke on AgentCore Runtime
    this.lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*`,
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*/runtime-endpoint/*`,
      ],
    }));
    this.lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:GetAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*`,
      ],
    }));

    // A2A keys (read + best-effort lastUsedAt update)
    props.a2aKeysTable.grantReadWriteData(this.lambda);

    // Imported agents table — explicit (grantReadData on ITable misses GSI ARN)
    this.lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["dynamodb:GetItem", "dynamodb:Query"],
      resources: [
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents`,
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents/index/*`,
      ],
    }));

    this.functionUrl = this.lambda.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    new cdk.CfnOutput(this, "FunctionUrl", { value: this.functionUrl.url });
  }
}
