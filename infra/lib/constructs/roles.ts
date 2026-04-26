import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface AgentCoreRolesProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

export class AgentCoreRoles extends Construct {
  public readonly subAgentRoleArn: string;
  public readonly metaAgentRoleArn: string;
  public readonly evaluatorRoleArn: string;
  public readonly schedulerTargetRoleArn: string;
  public readonly basicRoleArn: string;

  constructor(scope: Construct, id: string, props: AgentCoreRolesProps) {
    super(scope, id);

    const trustPrincipal = new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
      conditions: {
        StringEquals: { "aws:SourceAccount": props.accountId },
      },
    });

    // ─── Baseline: runtime infrastructure (all roles need these) ───
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
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${props.accountId}:inference-profile/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:ListAgentRuntimes",
          "bedrock-agentcore:GetAgentRuntime",
          "bedrock-agentcore:DeleteAgentRuntime",
        ],
        // Invocations against ?qualifier=DEFAULT authorize against the
        // runtime-endpoint ARN, not the runtime ARN — MCP runtime calls
        // from sub-agents return 403 without the endpoint resource here.
        // Match the pattern the scheduler target role already uses below.
        resources: [
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`,
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*/runtime-endpoint/*`,
        ],
      }),
      // Read-only Gateway access so list_mcp_servers / list_mcp_target_tools
      // can enumerate MCP targets. Scoped to "*" because ListGateways has
      // no resource-level ARN, and gateway IDs aren't known at CDK-synth
      // time. The actions are read-only — no CreateGateway / DeleteTarget.
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:ListGateways",
          "bedrock-agentcore:GetGateway",
          "bedrock-agentcore:ListGatewayTargets",
          "bedrock-agentcore:GetGatewayTarget",
        ],
        resources: ["*"],
      }),
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:StartCodeInterpreterSession",
          "bedrock-agentcore:InvokeCodeInterpreter",
          "bedrock-agentcore:StopCodeInterpreterSession",
          "bedrock-agentcore:StartBrowserSession",
          "bedrock-agentcore:InvokeBrowser",
          "bedrock-agentcore:StopBrowserSession",
          "bedrock-agentcore:GetBrowserSession",
          "bedrock-agentcore:ListBrowserSessions",
          "bedrock-agentcore:SaveBrowserSessionProfile",
          "bedrock-agentcore:UpdateBrowserStream",
          "bedrock-agentcore:ConnectBrowserStream",
          "bedrock-agentcore:ConnectBrowserAutomationStream",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:code-interpreter-custom/*`,
          `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:browser-custom/*`,
        ],
      }),
    ];

    // ─── Sub-Agent data access (S3 + DDB) ───
    const subAgentDataStatements = [
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`arn:aws:s3:::${props.s3Bucket}/*`],
      }),
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [`arn:aws:s3:::${props.s3Bucket}`],
      }),
      new iam.PolicyStatement({
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [
          `arn:aws:s3:::${props.s3Bucket}/runs/*`,
          `arn:aws:s3:::${props.s3Bucket}/outputs/*`,
          `arn:aws:s3:::${props.s3Bucket}/uploads/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem"],
        resources: [
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills/index/*`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`,
          `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs/index/*`,
        ],
      }),
      new iam.PolicyStatement({
        actions: ["dynamodb:PutItem", "dynamodb:UpdateItem"],
        resources: [`arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`],
      }),
    ];

    // ─── Sub-Agent Role (logical ID "BasicRole" for CFN stability) ───
    const subAgentRole = new iam.Role(this, "BasicRole", {
      roleName: `AgentStudioSubAgent-basic-${props.region}`,
      assumedBy: trustPrincipal,
    });
    baselineStatements.forEach((s) => subAgentRole.addToPolicy(s));
    subAgentDataStatements.forEach((s) => subAgentRole.addToPolicy(s));
    // Sub-agents read their own secrets at cold start via the ARN list
    // passed by the Meta-Agent's deploy.py in AGENT_STUDIO_SECRET_ARNS.
    // Scope to the agent-studio/ prefix; NO ListSecrets (not resource-
    // scopable — Meta-Agent does the enumeration under its own role).
    // DescribeSecret is required by aws-secretsmanager-caching for its
    // rotation metadata check.
    subAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
      ],
      resources: [
        `arn:aws:secretsmanager:${props.region}:${props.accountId}:secret:agent-studio/*`,
      ],
    }));

    // AgentCore Memory — data plane (deployed Agents write CreateEvent,
    // read ListMemoryRecords + RetrieveMemoryRecords in their invoke() flow).
    subAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListMemoryRecords",
        "bedrock-agentcore:RetrieveMemoryRecords",
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:memory/*`,
      ],
    }));

    // ─── Meta-Agent Role ───
    const metaAgentRole = new iam.Role(this, "MetaAgentRole", {
      roleName: `AgentStudioMetaAgent-${props.region}`,
      assumedBy: trustPrincipal,
    });
    baselineStatements.forEach((s) => metaAgentRole.addToPolicy(s));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      resources: [`arn:aws:s3:::${props.s3Bucket}/*`],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3:ListBucket"],
      resources: [`arn:aws:s3:::${props.s3Bucket}`],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchGetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem"],
      resources: [
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-agents/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-tools/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-workspaces/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-skills/index/*`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs`,
        `arn:aws:dynamodb:${props.region}:${props.accountId}:table/agent-studio-runs/index/*`,
      ],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:CreateAgentRuntime",
        "bedrock-agentcore:UpdateAgentRuntime",
        "bedrock-agentcore:DeleteAgentRuntime",
        // CreateAgentRuntime implicitly provisions a DEFAULT endpoint, which
        // the control plane attributes back to the caller — so the Meta-Agent
        // needs the Endpoint actions too, or every create fails with
        // "not authorized to perform: CreateAgentRuntimeEndpoint". The CRUD
        // Lambda has these for the same reason (see constructs/api.ts).
        "bedrock-agentcore:CreateAgentRuntimeEndpoint",
        "bedrock-agentcore:UpdateAgentRuntimeEndpoint",
        "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
        "bedrock-agentcore:GetAgentRuntimeEndpoint",
        "bedrock-agentcore:ListAgentRuntimeEndpoints",
      ],
      resources: [`arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`],
    }));
    // CreateAgentRuntime also transparently provisions a WorkloadIdentity
    // under workload-identity-directory/default/ so the runtime can call
    // back to AgentCore-managed services (Identity Store etc). Missing this
    // surfaces as: "not authorized to perform: CreateWorkloadIdentity".
    //
    // The IAM authz actually checks against TWO distinct resource ARNs:
    //   - the directory itself  (.../workload-identity-directory/default)
    //   - the workload identity (.../workload-identity-directory/default/workload-identity/*)
    // The directory-level ARN is what CreateWorkloadIdentity's first authz
    // check sees; the per-identity ARN is what Get/Update/Delete check.
    // Grant both so there's no partial-permission pothole during create.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "bedrock-agentcore:CreateWorkloadIdentity",
        "bedrock-agentcore:GetWorkloadIdentity",
        "bedrock-agentcore:UpdateWorkloadIdentity",
        "bedrock-agentcore:DeleteWorkloadIdentity",
        "bedrock-agentcore:ListWorkloadIdentities",
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:workload-identity-directory/default`,
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:workload-identity-directory/default/workload-identity/*`,
      ],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [subAgentRole.roleArn, metaAgentRole.roleArn],
      conditions: {
        StringEquals: { "iam:PassedToService": "bedrock-agentcore.amazonaws.com" },
      },
    }));
    // ─── Workspace IAM role support ───
    // Meta-Agent passes dynamically-created workspace roles to AgentCore
    // when deploying agents bound to a workspace with a custom role.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [`arn:aws:iam::${props.accountId}:role/AgentStudio-ws-*`],
      conditions: {
        StringEquals: { "iam:PassedToService": "bedrock-agentcore.amazonaws.com" },
      },
    }));
    // SimulatePrincipalPolicy lets check_workspace_permissions verify
    // what actions a workspace role is allowed to perform before deploy.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["iam:SimulatePrincipalPolicy"],
      resources: [`arn:aws:iam::${props.accountId}:role/AgentStudio-ws-*`],
    }));

    // create_schedule hands schedulerTargetRoleArn to EventBridge Scheduler
    // so the service can assume it when firing a scheduled agent invocation.
    // Separate statement because the PassedToService string is different and
    // IAM requires condition-resource pairs be scoped together. Resource is
    // filled in lower down once `schedulerTargetRole` is defined — we can't
    // forward-reference it here without a closure, so apply the statement
    // after the role is created.
    // Meta-Agent reads/writes per-agent secrets for:
    //   - manage_secrets tools (set/list/delete under agent-studio/{ws}/{agent}/{key})
    //   - link_agent linked A2A keys blob (same prefix)
    //   - deploy.py assembling AGENT_STUDIO_SECRET_ARNS for sub-agent runtime
    // ListSecrets cannot be resource-scoped; it's needed to enumerate
    // which per-key secrets exist for an agent at deploy time.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "secretsmanager:CreateSecret",
        "secretsmanager:GetSecretValue",
        "secretsmanager:PutSecretValue",
        "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:DescribeSecret",
      ],
      resources: [
        `arn:aws:secretsmanager:${props.region}:${props.accountId}:secret:agent-studio/*`,
      ],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["secretsmanager:ListSecrets"],
      resources: ["*"],
    }));

    // EventBridge Scheduler — create_schedule tool provisions / updates
    // cron triggers for agents. Scoped to the default group (AgentCore
    // runtimes don't use custom groups); ListSchedules gets its own
    // statement because the action doesn't support resource-level
    // filtering in IAM (matches the CRUD Lambda pattern in api.ts).
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "scheduler:CreateSchedule",
        "scheduler:UpdateSchedule",
        "scheduler:DeleteSchedule",
        "scheduler:GetSchedule",
      ],
      resources: [
        `arn:aws:scheduler:${props.region}:${props.accountId}:schedule/default/*`,
      ],
    }));
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["scheduler:ListSchedules"],
      resources: ["*"],
    }));

    // check_agent_logs tool reads the runtime log stream. The baseline
    // policy above covers write-side actions only (CreateLogStream,
    // PutLogEvents); filter/describe are needed for read. These two
    // actions require the `:*` log-stream ARN form, not the bare
    // log-group ARN — the bare form is silently ignored.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["logs:FilterLogEvents", "logs:DescribeLogStreams"],
      resources: [
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*:*`,
      ],
    }));

    this.subAgentRoleArn = subAgentRole.roleArn;
    this.metaAgentRoleArn = metaAgentRole.roleArn;
    this.basicRoleArn = subAgentRole.roleArn;

    new cdk.CfnOutput(this, "SubAgentRoleArn", { value: subAgentRole.roleArn });
    new cdk.CfnOutput(this, "MetaAgentRoleArn", { value: metaAgentRole.roleArn });

    // ─── Evaluator execution role ───
    const evaluatorRole = new iam.Role(this, "EvaluatorExecutionRole", {
      roleName: `AgentStudioEvaluatorExecution-${props.region}`,
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": props.accountId },
        },
      }),
    });
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: [
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
        "logs:FilterLogEvents",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ],
      resources: [
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:aws/spans`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:aws/spans:*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/bedrock-agentcore/evaluations/*`,
        `arn:aws:logs:${props.region}:${props.accountId}:log-group:/aws/vendedlogs/bedrock-agentcore/evaluation/*`,
      ],
    }));
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: ["logs:DescribeLogGroups"],
      resources: ["*"],
    }));
    evaluatorRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse", "bedrock:ConverseStream"],
      resources: [`arn:aws:bedrock:${props.region}::foundation-model/*`],
    }));
    this.evaluatorRoleArn = evaluatorRole.roleArn;

    new cdk.CfnOutput(this, "EvaluatorRoleArn", {
      value: evaluatorRole.roleArn,
      exportName: "AgentStudio-EvaluatorRoleArn",
    });

    // ─── Scheduler target role ───
    const schedulerTargetRole = new iam.Role(this, "SchedulerTargetRole", {
      roleName: `AgentStudioSchedulerTargetRole-${props.region}`,
      assumedBy: new iam.ServicePrincipal("scheduler.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": props.accountId },
        },
      }),
    });
    schedulerTargetRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*`,
        `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/*/runtime-endpoint/*`,
      ],
    }));
    schedulerTargetRole.addToPolicy(new iam.PolicyStatement({
      actions: ["lambda:InvokeFunction"],
      resources: [
        `arn:aws:lambda:${props.region}:${props.accountId}:function:agent-studio-schedule-runner`,
      ],
    }));
    this.schedulerTargetRoleArn = schedulerTargetRole.roleArn;

    // Meta-Agent passes the scheduler target role to EventBridge Scheduler
    // when create_schedule provisions a new trigger. Uses the construct's
    // own token reference so a future rename can't silently drift the
    // resource string.
    metaAgentRole.addToPolicy(new iam.PolicyStatement({
      actions: ["iam:PassRole"],
      resources: [schedulerTargetRole.roleArn],
      conditions: {
        StringEquals: { "iam:PassedToService": "scheduler.amazonaws.com" },
      },
    }));

    new cdk.CfnOutput(this, "SchedulerTargetRoleArn", {
      value: schedulerTargetRole.roleArn,
      exportName: "AgentStudio-SchedulerTargetRoleArn",
    });
  }
}
