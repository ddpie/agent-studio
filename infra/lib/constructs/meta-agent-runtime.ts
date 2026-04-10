import * as cdk from "aws-cdk-lib";
import * as bedrockagentcore from "aws-cdk-lib/aws-bedrockagentcore";
import { Construct } from "constructs";

export interface MetaAgentRuntimeProps {
  s3Bucket: string;
  roleArn: string;
  region: string;
  accountId: string;
  existingRuntimeId?: string;
}

export class MetaAgentRuntime extends Construct {
  public readonly agentRuntimeId: string;
  public readonly agentRuntimeArn: string;

  constructor(scope: Construct, id: string, props: MetaAgentRuntimeProps) {
    super(scope, id);

    if (props.existingRuntimeId) {
      // Existing Runtime — reference by ID, don't create
      this.agentRuntimeId = props.existingRuntimeId;
      this.agentRuntimeArn = `arn:aws:bedrock-agentcore:${props.region}:${props.accountId}:runtime/${props.existingRuntimeId}`;
    } else {
      // New CfnRuntime — CloudFormation handles READY wait natively
      const metaAgentS3Key = "agents/agentStudioMeta/deployment.zip";

      const runtime = new bedrockagentcore.CfnRuntime(this, "Runtime", {
        agentRuntimeName: "agentStudioMeta",
        description: "Agent Studio Meta-Agent — orchestrates sub-agent lifecycle",
        roleArn: props.roleArn,
        agentRuntimeArtifact: {
          codeConfiguration: {
            code: { s3: { bucket: props.s3Bucket, prefix: metaAgentS3Key } },
            runtime: "PYTHON_3_10",
            entryPoint: ["main.py"],
          },
        },
        networkConfiguration: { networkMode: "PUBLIC" },
        protocolConfiguration: "HTTP",
      });

      // filesystemConfigurations not in L1 types — use escape hatch
      runtime.addPropertyOverride("FilesystemConfigurations", [{
        SessionStorage: { MountPath: "/mnt/workspace" },
      }]);

      runtime.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

      this.agentRuntimeId = runtime.attrAgentRuntimeId;
      this.agentRuntimeArn = runtime.attrAgentRuntimeArn;
    }

    new cdk.CfnOutput(this, "MetaAgentId", { value: this.agentRuntimeId });
    new cdk.CfnOutput(this, "MetaAgentArn", { value: this.agentRuntimeArn });
  }
}
