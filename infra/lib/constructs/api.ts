import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface ApiProps {
  config: AgentStudioConfig;
  cognitoUserPoolId: string;
  cognitoClientId: string;
  cognitoUserPoolArn: string;
  metaAgentArn: string;
  evaluatorRoleArn: string;
  schedulerTargetRoleArn: string;
  workspacesTable: dynamodb.Table;
  agentsTable: dynamodb.ITable;
  skillsTable: dynamodb.Table;
  toolsTable: dynamodb.ITable;
  a2aKeysTable: dynamodb.Table;
  runsTable: dynamodb.Table;
  originVerifyValue: string;
  scheduleRunnerLambdaArn: string;
  /** ARN of the AgentStudioWorkspaceCeiling permission boundary policy. */
  workspaceBoundaryArn: string;
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
        COGNITO_USER_POOL_ID: props.cognitoUserPoolId,
        COGNITO_CLIENT_ID: props.cognitoClientId,
        META_AGENT_ARN: props.metaAgentArn,
        EVALUATOR_ROLE_ARN: props.evaluatorRoleArn,
        SPANS_LOG_GROUP: "aws/spans",
        MCP_GATEWAY_URL: props.config.mcpGatewayUrl || '',
        A2A_KEYS_TABLE: props.a2aKeysTable.tableName,
        RUNS_TABLE: props.runsTable.tableName,
        ORIGIN_VERIFY_VALUE: props.originVerifyValue,
        AGENT_STUDIO_ACCOUNT_ID: props.config.accountId,
        AGENTCORE_REGION: props.config.region,
        // Scheduler assumes this dedicated role (trust:
        // scheduler.amazonaws.com) to invoke sub-agent runtimes. Kept
        // separate from the sub-agent execution roles so scheduler
        // trust doesn't widen those roles' blast radius.
        SCHEDULER_TARGET_ROLE_ARN: props.schedulerTargetRoleArn,
        SCHEDULE_RUNNER_LAMBDA_ARN: props.scheduleRunnerLambdaArn,
        WORKSPACE_BOUNDARY_ARN: props.workspaceBoundaryArn,
      },
    });

    // CDK-managed tables: use grant
    props.workspacesTable.grantReadWriteData(this.crudLambda);
    props.skillsTable.grantReadWriteData(this.crudLambda);
    props.a2aKeysTable.grantReadWriteData(this.crudLambda);
    props.runsTable.grantReadWriteData(this.crudLambda);

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
        `arn:aws:s3:::${props.config.s3Bucket}/mcp/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/mcp-runtime/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/runs/*`,
        `arn:aws:s3:::${props.config.s3Bucket}/chat/*`,
      ],
    }));
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["s3:ListBucket"],
      resources: [`arn:aws:s3:::${props.config.s3Bucket}`],
      conditions: {
        StringLike: { "s3:prefix": ["agents/*", "skills/*", "uploads/*", "tools/*", "outputs/*", "workspaces/*", "mcp/*", "mcp-runtime/*", "runs/*", "chat/*"] },
      },
    }));

    // Secrets Manager — scoped actions
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "secretsmanager:CreateSecret",
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
        "secretsmanager:DeleteSecret",
        // kiro_key.py Describe + TagResource for updatedBy metadata.
        "secretsmanager:DescribeSecret",
        "secretsmanager:TagResource",
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

    // kiro_key.py: `GET /kiro-key/usage` calls the Meta-Agent runtime
    // with action=get_usage to ask Kiro for the caller workspace's
    // credit balance. Scoped to the Meta-Agent ARN only (not *-runtime
    // wildcard) so this role can't fire arbitrary agent invocations.
    if (props.metaAgentArn) {
      this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
        actions: ["bedrock-agentcore:InvokeAgentRuntime"],
        resources: [
          props.metaAgentArn,
          `${props.metaAgentArn}/runtime-endpoint/*`,
        ],
      }));
    }

    // Sprint 1 D1-D3: runtime observability passthrough endpoints
    // (crud/runtime.py). Read-only against sub-agent runtimes; endpoint
    // mutations for blue/green UI.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:GetAgentRuntime",
        "bedrock-agentcore:ListAgentRuntimes",
        "bedrock-agentcore:ListAgentRuntimeVersions",
        "bedrock-agentcore:ListAgentRuntimeEndpoints",
        "bedrock-agentcore:CreateAgentRuntimeEndpoint",
        "bedrock-agentcore:UpdateAgentRuntimeEndpoint",
        "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*`,
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*/runtime-endpoint/*`,
      ],
    }));

    // Sprint 2 F5: create eval config from workspaces.create + read eval
    // results from CloudWatch Logs Insights.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:DescribeLogGroups",
        "bedrock-agentcore:CreateOnlineEvaluationConfig",
        "bedrock-agentcore:ListOnlineEvaluationConfigs",
        "bedrock-agentcore:GetOnlineEvaluationConfig",
        "bedrock-agentcore:DeleteOnlineEvaluationConfig",
        "bedrock-agentcore:UpdateOnlineEvaluationConfig",
      ],
      resources: ["*"],
    }));

    // AgentCore Memory — control plane (CreateMemory used by workspace creation
    // and repair endpoint; DeleteMemory used by workspace deletion).
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:CreateMemory",
        "bedrock-agentcore:DeleteMemory",
        "bedrock-agentcore:ListMemories",
      ],
      resources: ["*"],
    }));

    // AgentCore Memory — data plane (end-user memory drawer: list/get/delete
    // individual memory records in lambda/crud/memories.py).
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:GetMemoryRecord",
        "bedrock-agentcore:DeleteMemoryRecord",
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:memory/*`,
      ],
    }));

    // Sprint 3: inline agent log viewer (crud/logs.py). FilterLogEvents
    // on the sub-agent runtime log groups. The action requires BOTH the
    // log-group ARN and the log-stream (`:*`) ARN to be allowed — AWS
    // rejects the call with AccessDenied otherwise. Scoped to agentcore
    // runtimes so this role can't read unrelated log groups.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "logs:FilterLogEvents",
        "logs:DescribeLogStreams",
        "logs:GetLogEvents",
      ],
      resources: [
        `arn:aws:logs:${props.config.region}:${props.config.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*:*`,
        `arn:aws:logs:${props.config.region}:${props.config.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
      ],
    }));

    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [props.evaluatorRoleArn],
      conditions: {
        StringEquals: { "iam:PassedToService": "bedrock-agentcore.amazonaws.com" },
      },
    }));

    // Hydrate workspace member identities (display_name/email) by calling
    // AdminGetUser from workspaces.get_workspace. Scoped to the single user
    // pool — without this the members tab shows raw Cognito sub UUIDs.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["cognito-idp:AdminGetUser"],
      resources: [props.cognitoUserPoolArn],
    }));

    // EventBridge Scheduler — per-agent cron triggers (crud/schedules.py).
    // Resource is scoped to the default group + agent-studio-* name
    // prefix so this role can't touch unrelated schedules.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "scheduler:ListSchedules",
        "scheduler:CreateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
        "scheduler:UpdateSchedule",
      ],
      resources: [
        `arn:aws:scheduler:${props.config.region}:${props.config.accountId}:schedule/default/agent-studio-*`,
      ],
    }));
    // ListSchedules at the account level does not support resource-level
    // filtering in IAM; the SDK call itself uses NamePrefix to limit
    // results. Without this, ListSchedules returns AccessDenied.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["scheduler:ListSchedules"],
      resources: ["*"],
    }));
    // Scheduler invokes the agent runtime via the dedicated scheduler
    // target role. Scoped PassRole → only this ARN, only for scheduler.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [props.schedulerTargetRoleArn],
      conditions: {
        StringEquals: { "iam:PassedToService": "scheduler.amazonaws.com" },
      },
    }));

    // ─── Workspace IAM role management ───
    // CRUD Lambda creates/manages per-workspace IAM roles at runtime
    // (POST /api/workspaces/{wsId}/role, grant-mcp, revoke-mcp).
    // The iam:PermissionsBoundary condition ensures CreateRole can only
    // create roles that have the WorkspaceCeiling boundary attached.
    // Note: this condition key is only evaluated by CreateRole and
    // PutRolePermissionsBoundary — other actions silently ignore it.
    // Security is still maintained because identity policy ∩ boundary.
    // CreateRole with mandatory boundary — condition enforced by IAM
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["iam:CreateRole"],
      resources: [`arn:aws:iam::${props.config.accountId}:role/AgentStudio-ws-*`],
      conditions: {
        StringEquals: {
          "iam:PermissionsBoundary": props.workspaceBoundaryArn,
        },
      },
    }));
    // All other IAM actions — no condition (iam:PermissionsBoundary
    // condition key is only valid for CreateRole/PutRolePermissionsBoundary;
    // IAM denies requests where the condition key is absent from context).
    // Security: boundary intersection caps effective permissions regardless.
    this.crudLambda.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        "iam:DeleteRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:TagRole",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:SimulatePrincipalPolicy",
      ],
      resources: [`arn:aws:iam::${props.config.accountId}:role/AgentStudio-ws-*`],
    }));

    // REST API with Cognito authorizer
    const userPool = cognito.UserPool.fromUserPoolArn(this, "UserPool", props.cognitoUserPoolArn);
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, "CognitoAuthorizer", {
      cognitoUserPools: [userPool],
      authorizerName: "agent-studio-cognito",
    });

    // Direct APIGW URL hits (bypassing CloudFront) are rejected in the
    // Lambda by the origin-verify header check (lambda/crud/handler.py).
    // APIGW resource policies don't support aws:RequestHeader conditions,
    // so the gateway itself can't enforce this — but the Lambda always runs
    // after the Cognito authorizer, and the check happens before routing.
    this.restApi = new apigateway.RestApi(this, "RestApi", {
      restApiName: "agent-studio-api",
      deployOptions: { stageName: "prod" },
    });

    const apiResource = this.restApi.root.addResource("api");
    apiResource.addProxy({
      defaultIntegration: new apigateway.LambdaIntegration(this.crudLambda),
      anyMethod: true,
      defaultMethodOptions: {
        authorizationType: apigateway.AuthorizationType.COGNITO,
        authorizer,
      },
    });
  }
}
