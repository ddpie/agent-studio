import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { AgentStudioConfig } from "./config";
import { Database } from "./constructs/database";

export class AgentStudioStack extends cdk.Stack {
  constructor(scope: Construct, id: string, config: AgentStudioConfig, props?: cdk.StackProps) {
    super(scope, id, props);

    const database = new Database(this, "Database", {
      existingAgentsTableName: "agent-studio-agents",
      existingToolsTableName: "agent-studio-tools",
    });
  }
}
