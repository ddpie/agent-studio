import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface McpReconcilerProps {
  config: AgentStudioConfig;
  workspacesTable: dynamodb.ITable;
}

/**
 * Hourly reconciler for stuck MCP runtime states (spec §6.5).
 *
 * Scans workspaces for mcp_runtimes.*.status ∈ {CREATING|UPDATING|DELETING}
 * with updated_at > 15min ago. Calls get_agent_runtime, updates DDB if
 * control-plane state has advanced. READ-ONLY on runtimes — never deletes.
 */
export class McpReconciler extends Construct {
  public readonly lambda: lambda.Function;

  constructor(scope: Construct, id: string, props: McpReconcilerProps) {
    super(scope, id);

    const { region, accountId } = props.config;

    this.lambda = new lambda.Function(this, "Lambda", {
      functionName: "agent-studio-mcp-reconciler",
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/reconciler"), {
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

    // IAM: read-only on runtimes, read+update on workspaces table.
    this.lambda.addToRolePolicy(new iam.PolicyStatement({
      sid: "McpRuntimeRead",
      actions: ["bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:ListAgentRuntimes"],
      resources: [
        `arn:aws:bedrock-agentcore:${region}:${accountId}:runtime/asmcp_*`,
        // ListAgentRuntimes is account-level
        "*",
      ],
    }));
    props.workspacesTable.grantReadWriteData(this.lambda);

    // Hourly trigger
    new events.Rule(this, "HourlyRule", {
      ruleName: "agent-studio-mcp-reconciler-hourly",
      schedule: events.Schedule.rate(cdk.Duration.hours(1)),
      targets: [new targets.LambdaFunction(this.lambda)],
    });

    new cdk.CfnOutput(this, "McpReconcilerArn", { value: this.lambda.functionArn });
  }
}
