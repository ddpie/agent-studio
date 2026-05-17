import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface ChannelsProps {
  config: AgentStudioConfig;
  agentsTable: dynamodb.ITable;
}

/**
 * Channel Integration infrastructure.
 *
 * Provisions DynamoDB tables, networking, ECS Fargate cluster, Lambda
 * functions, EventBridge rules, IAM roles, and CloudWatch log groups
 * for the channel relay subsystem.
 */
export class Channels extends Construct {
  public readonly channelsTable: dynamodb.Table;
  public readonly tokensTable: dynamodb.Table;
  public readonly historyTable: dynamodb.Table;
  public readonly inflightTable: dynamodb.Table;
  public readonly workerLambda: lambda.Function;
  public readonly reaperLambda: lambda.Function;

  constructor(scope: Construct, id: string, props: ChannelsProps) {
    super(scope, id);

    const { region, accountId } = props.config;
    const { agentsTable } = props;

    // ─────────────────────────────────────────────────────────────────────
    // DynamoDB Tables
    // ─────────────────────────────────────────────────────────────────────

    this.channelsTable = new dynamodb.Table(this, "ChannelsTable", {
      tableName: "agent-studio-channels",
      partitionKey: { name: "workspaceId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.tokensTable = new dynamodb.Table(this, "TokensTable", {
      tableName: "agent-studio-channel-tokens",
      partitionKey: { name: "channelId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.historyTable = new dynamodb.Table(this, "HistoryTable", {
      tableName: "agent-studio-channel-history",
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.inflightTable = new dynamodb.Table(this, "InflightTable", {
      tableName: "agent-studio-channel-inflight",
      partitionKey: { name: "cardId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // ─────────────────────────────────────────────────────────────────────
    // SQS FIFO Queue — serializes messages per conversation
    // ─────────────────────────────────────────────────────────────────────

    const workerDlq = new sqs.Queue(this, "WorkerDLQ", {
      queueName: "agent-studio-channel-worker-dlq.fifo",
      fifo: true,
      retentionPeriod: cdk.Duration.days(14),
    });

    const workerQueue = new sqs.Queue(this, "WorkerQueue", {
      queueName: "agent-studio-channel-worker.fifo",
      fifo: true,
      contentBasedDeduplication: true,
      visibilityTimeout: cdk.Duration.seconds(910),
      retentionPeriod: cdk.Duration.days(1),
      deadLetterQueue: { queue: workerDlq, maxReceiveCount: 3 },
    });

    // ─────────────────────────────────────────────────────────────────────
    // Networking — dedicated VPC, public subnets only, no NAT
    // ─────────────────────────────────────────────────────────────────────

    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
      ],
    });

    const securityGroup = new ec2.SecurityGroup(this, "RelaySG", {
      vpc,
      description: "Channel relay - egress 443 only, zero ingress",
      allowAllOutbound: false,
    });
    securityGroup.addEgressRule(
      ec2.Peer.anyIpv4(),
      ec2.Port.tcp(443),
      "HTTPS/WSS egress"
    );

    // ─────────────────────────────────────────────────────────────────────
    // ECS — Fargate cluster + ECR repository + task definition
    // ─────────────────────────────────────────────────────────────────────

    const cluster = new ecs.Cluster(this, "Cluster", {
      clusterName: "agent-studio-channels",
      vpc,
    });

    const ecrRepo = new ecr.Repository(this, "EcrRepo", {
      repositoryName: "agent-studio-channel-relay",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Relay IAM role (ECS task role)
    const relayRole = new iam.Role(this, "RelayRole", {
      roleName: `agent-studio-channel-relay`,
      assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": accountId },
        },
      }),
    });

    // CloudWatch log groups
    const relayLogGroup = new logs.LogGroup(this, "RelayLogGroup", {
      logGroupName: "/agent-studio/channel-relay",
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const workerLogGroup = new logs.LogGroup(this, "WorkerLogGroup", {
      logGroupName: "/agent-studio/channel-worker",
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Task definition — CDK builds + pushes the Docker image automatically
    const taskDef = new ecs.FargateTaskDefinition(this, "TaskDef", {
      cpu: 256,
      memoryLimitMiB: 512,
      taskRole: relayRole,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    taskDef.addContainer("relay", {
      image: ecs.ContainerImage.fromAsset("../lambda/channels/relay", {
        platform: cdk.aws_ecr_assets.Platform.LINUX_ARM64,
      }),
      logging: ecs.LogDrivers.awsLogs({
        logGroup: relayLogGroup,
        streamPrefix: "relay",
      }),
      environment: {
        CHANNELS_TABLE: this.channelsTable.tableName,
        TOKENS_TABLE: this.tokensTable.tableName,
        WORKER_QUEUE_URL: workerQueue.queueUrl,
        REGION: region,
      },
    });

    // ─────────────────────────────────────────────────────────────────────
    // Lambda — Channel Worker
    // ─────────────────────────────────────────────────────────────────────

    const workerRole = new iam.Role(this, "WorkerRole", {
      roleName: `agent-studio-channel-worker`,
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });

    this.workerLambda = new lambda.Function(this, "WorkerHandler", {
      functionName: "agent-studio-channel-worker",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/channels/worker"), {
        assetHashType: cdk.AssetHashType.SOURCE,
        bundling: {
          image: lambda.Runtime.NODEJS_22_X.bundlingImage,
          command: ["bash", "-c",
            "cp package.json package-lock.json *.mjs /asset-output/ && " +
            "cd /asset-output && HOME=/tmp npm ci --omit=dev"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(900),
      memorySize: 512,
      role: workerRole,
      logGroup: workerLogGroup,
      environment: {
        CHANNELS_TABLE: this.channelsTable.tableName,
        TOKENS_TABLE: this.tokensTable.tableName,
        HISTORY_TABLE: this.historyTable.tableName,
        INFLIGHT_TABLE: this.inflightTable.tableName,
        AGENTS_TABLE: agentsTable.tableName,
        REGION: region,
        ACCOUNT_ID: accountId,
      },
    });

    // Wire SQS FIFO → Worker Lambda (batchSize=1 for serialized processing)
    this.workerLambda.addEventSource(new lambdaEventSources.SqsEventSource(workerQueue, {
      batchSize: 1,
    }));

    // ─────────────────────────────────────────────────────────────────────
    // Lambda — Card Reaper
    // ─────────────────────────────────────────────────────────────────────

    const reaperRole = new iam.Role(this, "ReaperRole", {
      roleName: `agent-studio-card-reaper`,
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });

    this.reaperLambda = new lambda.Function(this, "ReaperHandler", {
      functionName: "agent-studio-card-reaper",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/channels/reaper"), {
        assetHashType: cdk.AssetHashType.SOURCE,
        bundling: {
          image: lambda.Runtime.NODEJS_22_X.bundlingImage,
          command: ["bash", "-c",
            "cp package.json package-lock.json *.mjs /asset-output/ && " +
            "cd /asset-output && HOME=/tmp npm ci --omit=dev"
          ],
        },
      }),
      timeout: cdk.Duration.seconds(30),
      memorySize: 128,
      role: reaperRole,
      environment: {
        INFLIGHT_TABLE: this.inflightTable.tableName,
        CHANNELS_TABLE: this.channelsTable.tableName,
        TOKENS_TABLE: this.tokensTable.tableName,
      },
    });

    // ─────────────────────────────────────────────────────────────────────
    // IAM — Relay Role policies
    // ─────────────────────────────────────────────────────────────────────

    relayRole.addToPolicy(new iam.PolicyStatement({
      sid: "SendToWorkerQueue",
      actions: ["sqs:SendMessage"],
      resources: [workerQueue.queueArn],
    }));

    relayRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoRead",
      actions: ["dynamodb:GetItem", "dynamodb:Query"],
      resources: [
        this.channelsTable.tableArn,
        this.tokensTable.tableArn,
      ],
    }));

    relayRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoHeartbeat",
      actions: ["dynamodb:PutItem", "dynamodb:UpdateItem"],
      resources: [this.channelsTable.tableArn],
    }));

    relayRole.addToPolicy(new iam.PolicyStatement({
      sid: "Secrets",
      actions: ["secretsmanager:GetSecretValue"],
      resources: [
        `arn:aws:secretsmanager:${region}:${accountId}:secret:agent-studio/channels/*`,
      ],
    }));

    relayRole.addToPolicy(new iam.PolicyStatement({
      sid: "Logs",
      actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
      resources: [relayLogGroup.logGroupArn, `${relayLogGroup.logGroupArn}:*`],
    }));

    // ─────────────────────────────────────────────────────────────────────
    // IAM — Worker Role policies
    // ─────────────────────────────────────────────────────────────────────

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "AgentCoreInvoke",
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/*`,
      ],
    }));

    // Explicit deny on MCP runtimes — isolate from workspace MCP
    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DenyMcpInvoke",
      effect: iam.Effect.DENY,
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/asmcp_*`,
      ],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoReadChannelsHistory",
      actions: ["dynamodb:GetItem", "dynamodb:Query"],
      resources: [
        this.channelsTable.tableArn,
        this.historyTable.tableArn,
      ],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoReadAgents",
      actions: ["dynamodb:GetItem", "dynamodb:Query"],
      resources: [
        agentsTable.tableArn,
        `${agentsTable.tableArn}/index/*`,
      ],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoWriteHistoryInflight",
      actions: ["dynamodb:PutItem", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem"],
      resources: [
        this.historyTable.tableArn,
        this.inflightTable.tableArn,
      ],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoTokens",
      actions: ["dynamodb:GetItem", "dynamodb:PutItem"],
      resources: [this.tokensTable.tableArn],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoChannelStats",
      actions: ["dynamodb:UpdateItem"],
      resources: [this.channelsTable.tableArn],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "Secrets",
      actions: ["secretsmanager:GetSecretValue"],
      resources: [
        `arn:aws:secretsmanager:${region}:${accountId}:secret:agent-studio/channels/*`,
      ],
    }));

    workerRole.addToPolicy(new iam.PolicyStatement({
      sid: "Logs",
      actions: [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ],
      resources: [workerLogGroup.logGroupArn, `${workerLogGroup.logGroupArn}:*`],
    }));

    // ─────────────────────────────────────────────────────────────────────
    // IAM — Reaper Role policies
    // ─────────────────────────────────────────────────────────────────────

    reaperRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoInflight",
      actions: ["dynamodb:Scan", "dynamodb:DeleteItem"],
      resources: [this.inflightTable.tableArn],
    }));

    reaperRole.addToPolicy(new iam.PolicyStatement({
      sid: "DynamoRead",
      actions: ["dynamodb:GetItem"],
      resources: [
        this.channelsTable.tableArn,
        this.tokensTable.tableArn,
      ],
    }));

    reaperRole.addToPolicy(new iam.PolicyStatement({
      sid: "Logs",
      actions: [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ],
      resources: [
        `arn:aws:logs:${cdk.Stack.of(this).region}:${cdk.Stack.of(this).account}:log-group:/aws/lambda/agent-studio-card-reaper:*`,
      ],
    }));

    // ─────────────────────────────────────────────────────────────────────
    // EventBridge — Card reaper schedule (every 5 minutes)
    // ─────────────────────────────────────────────────────────────────────

    const reaperRule = new events.Rule(this, "ReaperSchedule", {
      ruleName: "agent-studio-card-reaper-schedule",
      schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
    });
    reaperRule.addTarget(new targets.LambdaFunction(this.reaperLambda));
  }
}
