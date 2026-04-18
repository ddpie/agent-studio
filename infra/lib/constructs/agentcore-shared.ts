import { Construct } from "constructs";
import { CfnOutput } from "aws-cdk-lib";
import { CfnCodeInterpreterCustom, CfnBrowserCustom } from "aws-cdk-lib/aws-bedrockagentcore";

export interface AgentCoreSharedProps {
  executionRoleArn: string;
  codeInterpreterName?: string;
  browserName?: string;
}

/**
 * Account-shared CodeInterpreter + Browser for Agent Studio sub-agents.
 *
 * `run_command` routes into the CodeInterpreter; `fetch_webpage` routes
 * into the Browser via CDP. IDs are exposed as CfnOutputs so
 * deploy-agentcore.sh can forward them to sub-agent runtimes via env vars.
 */
export class AgentCoreShared extends Construct {
  public readonly codeInterpreterId: string;
  public readonly browserId: string;

  constructor(scope: Construct, id: string, props: AgentCoreSharedProps) {
    super(scope, id);

    const ci = new CfnCodeInterpreterCustom(this, "CodeInterpreter", {
      name: props.codeInterpreterName ?? "agentstudio_ci_shared",
      description: "Shared Code Interpreter for Agent Studio sub-agents",
      executionRoleArn: props.executionRoleArn,
      networkConfiguration: { networkMode: "PUBLIC" },
    });

    const browser = new CfnBrowserCustom(this, "Browser", {
      name: props.browserName ?? "agentstudio_br_shared",
      description: "Shared Browser for Agent Studio sub-agents",
      executionRoleArn: props.executionRoleArn,
      networkConfiguration: { networkMode: "PUBLIC" },
    });

    this.codeInterpreterId = ci.attrCodeInterpreterId;
    this.browserId = browser.attrBrowserId;

    new CfnOutput(this, "CodeInterpreterId", {
      value: this.codeInterpreterId,
      exportName: "AgentStudio-CodeInterpreterId",
    });
    new CfnOutput(this, "BrowserId", {
      value: this.browserId,
      exportName: "AgentStudio-BrowserId",
    });
  }
}
