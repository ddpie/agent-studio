import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface McpDrainProps {
  config: AgentStudioConfig;
  workspacesTable: dynamodb.ITable;
}

/**
 * Async workspace-delete drain (spec D14).
 *
 * DELETE /workspaces/{id} enqueues one SQS message per enabled MCP runtime
 * (action: delete_runtime) + one final cleanup message (action: cleanup_ws).
 * Worker pops each message, deletes runtime, waits DELETED, and removes
 * the grant statements from WorkspaceGrants. Cleanup message deletes the
 * workspace role + META once mcp_runtimes is empty.
 */
export class McpDrain extends Construct {
  public readonly queue: sqs.Queue;
  public readonly worker: lambda.Function;

  constructor(scope: Construct, id: string, props: McpDrainProps) {
    super(scope, id);

    const { region, accountId } = props.config;

    const dlq = new sqs.Queue(this, "DLQ", {
      queueName: "agent-studio-mcp-drain-dlq",
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    this.queue = new sqs.Queue(this, "Queue", {
      queueName: "agent-studio-mcp-drain",
      // Visibility > worker max wall-clock so message doesn't reappear mid-delete
      visibilityTimeout: cdk.Duration.seconds(360),
      retentionPeriod: cdk.Duration.days(4),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      deadLetterQueue: {
        queue: dlq,
        maxReceiveCount: 3,
      },
    });

    this.worker = new lambda.Function(this, "Worker", {
      functionName: "agent-studio-mcp-drain-worker",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/drain"), {
        assetHashType: cdk.AssetHashType.SOURCE,
      }),
      timeout: cdk.Duration.seconds(300),
      memorySize: 256,
      environment: {
        AGENT_STUDIO_REGION: region,
        AGENT_STUDIO_ACCOUNT_ID: accountId,
        WORKSPACES_TABLE: props.workspacesTable.tableName,
      },
    });

    // SQS → worker
    this.worker.addEventSource(new SqsEventSource(this.queue, {
      batchSize: 1,
      reportBatchItemFailures: false,
    }));

    // IAM: delete runtime, manage workspace role inline policies, update DDB.
    this.worker.addToRolePolicy(new iam.PolicyStatement({
      sid: "McpRuntimeControl",
      actions: [
        "bedrock-agentcore:DeleteAgentRuntime",
        "bedrock-agentcore:GetAgentRuntime",
        "bedrock-agentcore:DeleteResourcePolicy",
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/asmcp_*`,
      ],
    }));
    this.worker.addToRolePolicy(new iam.PolicyStatement({
      sid: "WorkspaceRoleInlinePolicies",
      actions: [
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:DeleteRole",
      ],
      resources: [`arn:aws:iam::${accountId}:role/AgentStudio-ws-*`],
    }));
    props.workspacesTable.grantReadWriteData(this.worker);

    new cdk.CfnOutput(this, "DrainQueueUrl", { value: this.queue.queueUrl });
    new cdk.CfnOutput(this, "DrainQueueArn", { value: this.queue.queueArn });
  }
}
