import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface ApiProps {
  config: AgentStudioConfig;
  workspacesTable: dynamodb.Table;
  agentsTable: dynamodb.ITable;
  skillsTable: dynamodb.Table;
  toolsTable: dynamodb.ITable;
}

export class Api extends Construct {
  public readonly restApi: apigateway.RestApi;
  public readonly crudLambda: lambda.Function;

  constructor(scope: Construct, id: string, props: ApiProps) {
    super(scope, id);

    // Bundle entire lambda/ dir so shared/ is accessible
    this.crudLambda = new lambda.Function(this, "CrudHandler", {
      functionName: "agent-studio-crud",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "crud.handler.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda"), {
        assetHashType: cdk.AssetHashType.SOURCE,
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: ["bash", "-c",
            "pip install -r crud/requirements.txt -t /asset-output && " +
            "cp -r crud /asset-output/crud && " +
            "cp -r shared /asset-output/shared"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(29),
      memorySize: 256,
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

    // CDK-managed tables: use grant
    props.workspacesTable.grantReadWriteData(this.crudLambda);
    props.skillsTable.grantReadWriteData(this.crudLambda);

    // Imported tables: explicit IAM policy (grant on ITable misses GSI ARN)
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
        "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan",
      ],
      resources: [
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents`,
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-agents/index/*`,
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-tools`,
        `arn:aws:dynamodb:${props.config.region}:${props.config.accountId}:table/agent-studio-tools/index/*`,
      ],
    }));

    // S3 access — scoped to specific prefixes, not entire bucket
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      resources: [
        `arn:aws:s3:::${props.config.s3Bucket}/agents/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/skills/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/uploads/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/tools/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/outputs/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/workspaces/*`,
      ],
    }));
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["s3:ListBucket"],
      resources: [`arn:aws:s3:::${props.config.s3Bucket}`],
      conditions: {
        StringLike: { "s3:prefix": ["agents/*", "skills/*", "uploads/*", "tools/*", "outputs/*", "workspaces/*"] },
      },
    }));

    // Secrets Manager — scoped actions
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "secretsmanager:CreateSecret",
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
        "secretsmanager:DeleteSecret",
      ],
      resources: [
        `arn:aws:secretsmanager:${props.config.region}:${props.config.accountId}:secret:agent-studio/*`,
      ],
    }));
    // ListSecrets does not support resource-level permissions
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["secretsmanager:ListSecrets"],
      resources: ["*"],
    }));

    // MCP Gateway discovery (bedrock-agentcore-control plane)
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:ListGateways",
        "bedrock-agentcore:ListGatewayTargets",
      ],
      resources: ["*"],
    }));

    // REST API
    this.restApi = new apigateway.RestApi(this, "RestApi", {
      restApiName: "agent-studio-api",
      deployOptions: { stageName: "prod" },
    });

    const apiResource = this.restApi.root.addResource("api");
    apiResource.addProxy({
      defaultIntegration: new apigateway.LambdaIntegration(this.crudLambda),
      anyMethod: true,
    });
  }
}
