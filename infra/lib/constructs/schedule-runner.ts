import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface ScheduleRunnerProps {
  config: AgentStudioConfig;
}

/**
 * Lambda that EventBridge Scheduler invokes to run an agent asynchronously.
 *
 * Drains the AgentCore streaming response to completion so the sub-agent's
 * _stream_and_record writes run records to DDB + S3. No user-facing HTTP
 * endpoint — invoked purely by EventBridge Scheduler (IAM-authed).
 */
export class ScheduleRunner extends Construct {
  public readonly lambda: lambda.Function;

  constructor(scope: Construct, id: string, props: ScheduleRunnerProps) {
    super(scope, id);

    this.lambda = new lambda.Function(this, "Handler", {
      functionName: "agent-studio-schedule-runner",
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../lambda/schedule-runner"), {
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
      timeout: cdk.Duration.seconds(900),
      memorySize: 256,
      environment: {
        ACCOUNT_ID: props.config.accountId,
      },
    });

    this.lambda.addToRolePolicy(new iam.PolicyStatement({
      actions: ["bedrock-agentcore:InvokeAgentRuntime"],
      resources: [`arn:aws:bedrock-agentcore:${props.config.region}:${props.config.accountId}:runtime/*`],
    }));

    new cdk.CfnOutput(this, "ScheduleRunnerArn", { value: this.lambda.functionArn });
  }
}
