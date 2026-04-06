import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { AgentStudioConfig } from "./config";
import { Database } from "./constructs/database";
import { Api } from "./constructs/api";
import { Invoke } from "./constructs/invoke";
import { Cdn } from "./constructs/cdn";

export class AgentStudioStack extends cdk.Stack {
  constructor(scope: Construct, id: string, config: AgentStudioConfig, props?: cdk.StackProps) {
    super(scope, id, props);

    if (config.region !== "us-east-1") {
      throw new Error("Stack must deploy to us-east-1 (WAF CLOUDFRONT scope requirement)");
    }

    const database = new Database(this, "Database", {
      existingAgentsTableName: "agent-studio-agents",
      existingToolsTableName: "agent-studio-tools",
    });

    const api = new Api(this, "Api", {
      config,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
      skillsTable: database.skillsTable,
      toolsTable: database.toolsTable,
    });

    const invoke = new Invoke(this, "Invoke", {
      config,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
      skillsTable: database.skillsTable,
      toolsTable: database.toolsTable,
    });

    const originVerifyValue = process.env.ORIGIN_VERIFY_SECRET;
    if (!originVerifyValue) throw new Error("Missing ORIGIN_VERIFY_SECRET env var — generate with: openssl rand -hex 32");

    const cdn = new Cdn(this, "Cdn", {
      config,
      restApi: api.restApi,
      functionUrl: invoke.functionUrl,
      originVerifyHeaderName: "x-origin-verify",
      originVerifyHeaderValue: originVerifyValue,
    });
  }
}
