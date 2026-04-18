import { Construct } from "constructs";
import { Stack } from "aws-cdk-lib";

export interface AgentCoreSharedProps {
  region: string;
  accountId: string;
  executionRoleArn: string;
}

/**
 * Surfaces the account-shared CodeInterpreter and Browser IDs to the
 * rest of the stack. Provisioning happens via
 * scripts/provision-agentcore-shared.sh because cdk-lib has not yet
 * published L1 resources for bedrock-agentcore CodeInterpreter/Browser.
 */
export class AgentCoreShared extends Construct {
  public readonly codeInterpreterId: string;
  public readonly browserId: string;

  constructor(scope: Construct, id: string, _props: AgentCoreSharedProps) {
    super(scope, id);
    const codeInterpreterId = Stack.of(this).node.tryGetContext("codeInterpreterId")
      ?? process.env.AGENT_STUDIO_CODE_INTERPRETER_ID
      ?? "";
    const browserId = Stack.of(this).node.tryGetContext("browserId")
      ?? process.env.AGENT_STUDIO_BROWSER_ID
      ?? "";
    this.codeInterpreterId = codeInterpreterId;
    this.browserId = browserId;
  }
}
