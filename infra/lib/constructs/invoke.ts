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
  skillsTable: dynamodb.ITable;
  toolsTable: dynamodb.ITable;
}

export class Invoke extends Construct {
  public readonly invokeLambda: lambda.Function;
  public readonly functionUrl: lambda.FunctionUrl;

  constructor(scope: Construct, id: string, props: InvokeProps) {
    super(scope, id);

    this.invokeLambda = new lambda.Function(this, "InvokeHandler", {
      functionName: "agent-studio-invoke",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "invoke.handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../lambda"), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: ["bash", "-c",
            "pip install -r invoke/requirements.txt -t /asset-output && " +
            "cp -r invoke /asset-output/invoke && " +
            "cp -r shared /asset-output/shared"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(300),
      memorySize: 512,
      environment: {
        S3_BUCKET: props.config.s3Bucket,
        WORKSPACES_TABLE: props.workspacesTable.tableName,
        AGENTS_TABLE: props.agentsTable.tableName,
        SKILLS_TABLE: props.skillsTable.tableName,
        TOOLS_TABLE: props.toolsTable.tableName,
        COGNITO_USER_POOL_ID: props.config.cognitoUserPoolId,
        COGNITO_CLIENT_ID: props.config.cognitoClientId,
        META_AGENT_ARN: `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/${props.config.metaAgentId}`,
      },
    });

    // Function URL with IAM auth (CloudFront OAC signs requests)
    this.functionUrl = this.invokeLambda.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.BUFFERED, // Switch to RESPONSE_STREAM in Plan 3 with Lambda Web Adapter
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
