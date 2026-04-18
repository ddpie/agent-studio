import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { AgentStudioConfig } from "./config";
import { Auth } from "./constructs/auth";
import { AgentCoreRoles } from "./constructs/roles";
import { MetaAgentRuntime } from "./constructs/meta-agent-runtime";
import { Database } from "./constructs/database";
import { Api } from "./constructs/api";
import { Invoke } from "./constructs/invoke";
import { Cdn } from "./constructs/cdn";

export interface AgentStudioStackProps extends cdk.StackProps {
  config: AgentStudioConfig;
  webAclArn: string;
  useExistingCognito?: boolean;
  existingCognitoUserPoolId?: string;
  existingCognitoClientId?: string;
  existingMetaAgentId?: string;
}

export class AgentStudioStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentStudioStackProps) {
    super(scope, id, props);
    const config = props.config;

    // Auth — Cognito User Pool + Client (or import existing)
    const auth = new Auth(this, "Auth", props.useExistingCognito ? {
      existingUserPoolId: props.existingCognitoUserPoolId!,
      existingClientId: props.existingCognitoClientId!,
    } : undefined);

    // IAM Roles for Sub-Agent permission tiers
    const roles = new AgentCoreRoles(this, "Roles", {
      region: config.region,
      accountId: config.accountId,
      s3Bucket: config.s3Bucket,
    });

    // Meta-Agent Runtime (CfnRuntime or existing reference)
    const metaAgent = new MetaAgentRuntime(this, "MetaAgentRuntime", {
      s3Bucket: config.s3Bucket,
      roleArn: roles.readonlyRoleArn,
      region: config.region,
      accountId: config.accountId,
      existingRuntimeId: props.existingMetaAgentId,
    });

    const database = new Database(this, "Database", {
      existingAgentsTableName: "agent-studio-agents",
      existingToolsTableName: "agent-studio-tools",
    });

    const api = new Api(this, "Api", {
      config,
      cognitoUserPoolId: auth.userPoolId,
      cognitoClientId: auth.userPoolClientId,
      cognitoUserPoolArn: auth.userPoolArn,
      metaAgentArn: metaAgent.agentRuntimeArn,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
      skillsTable: database.skillsTable,
      toolsTable: database.toolsTable,
    });

    const invoke = new Invoke(this, "Invoke", {
      config,
      cognitoUserPoolId: auth.userPoolId,
      cognitoClientId: auth.userPoolClientId,
      metaAgentArn: metaAgent.agentRuntimeArn,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
    });

    const originVerifyValue = process.env.ORIGIN_VERIFY_SECRET;
    if (!originVerifyValue) throw new Error("Missing ORIGIN_VERIFY_SECRET env var — generate with: openssl rand -hex 32");

    new Cdn(this, "Cdn", {
      config,
      restApi: api.restApi,
      functionUrl: invoke.functionUrl,
      invokeLambda: invoke.invokeLambda,
      originVerifyHeaderName: "x-origin-verify",
      originVerifyHeaderValue: originVerifyValue,
      webAclArn: props.webAclArn,
    });
  }
}
