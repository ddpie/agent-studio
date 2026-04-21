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
import { AgentCoreShared } from "./constructs/agentcore-shared";
import { A2aProxy } from "./constructs/a2a-proxy";
import { BaseDeployment } from "./constructs/base-deployment";
import * as path from "path";

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

    // Upload base/deployment.zip — the shared dependency layer every
    // sub-agent downloads. Build with: bash scripts/build-base-zip.sh
    new BaseDeployment(this, "BaseDeployment", {
      targetBucket: config.s3Bucket,
      zipPath: path.resolve(__dirname, "../../base/deployment.zip"),
    });

    // Account-shared CodeInterpreter + Browser. Sub-agents' run_command and
    // fetch_webpage tools route through these.
    const agentCoreShared = new AgentCoreShared(this, "AgentCoreShared", {
      executionRoleArn: roles.basicRoleArn,
    });

    // Meta-Agent Runtime (CfnRuntime or existing reference)
    const metaAgent = new MetaAgentRuntime(this, "MetaAgentRuntime", {
      s3Bucket: config.s3Bucket,
      roleArn: roles.metaAgentRoleArn,
      region: config.region,
      accountId: config.accountId,
      existingRuntimeId: props.existingMetaAgentId,
    });

    const database = new Database(this, "Database", {
      existingAgentsTableName: "agent-studio-agents",
      existingToolsTableName: "agent-studio-tools",
    });

    const originVerifyValue = process.env.ORIGIN_VERIFY_SECRET;
    if (!originVerifyValue) throw new Error("Missing ORIGIN_VERIFY_SECRET env var — generate with: openssl rand -hex 32");

    const api = new Api(this, "Api", {
      config,
      cognitoUserPoolId: auth.userPoolId,
      cognitoClientId: auth.userPoolClientId,
      cognitoUserPoolArn: auth.userPoolArn,
      metaAgentArn: metaAgent.agentRuntimeArn,
      evaluatorRoleArn: roles.evaluatorRoleArn,
      schedulerTargetRoleArn: roles.schedulerTargetRoleArn,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
      skillsTable: database.skillsTable,
      toolsTable: database.toolsTable,
      a2aKeysTable: database.a2aKeysTable,
      runsTable: database.runsTable,
      originVerifyValue,
    });

    const invoke = new Invoke(this, "Invoke", {
      config,
      cognitoUserPoolId: auth.userPoolId,
      cognitoClientId: auth.userPoolClientId,
      metaAgentArn: metaAgent.agentRuntimeArn,
      workspacesTable: database.workspacesTable,
      agentsTable: database.agentsTable,
    });

    const a2aProxy = new A2aProxy(this, "A2aProxy", {
      config,
      metaAgentArn: metaAgent.agentRuntimeArn,
      agentsTable: database.agentsTable,
      a2aKeysTable: database.a2aKeysTable,
      publicHost: process.env.AGENT_STUDIO_CLOUDFRONT_DOMAIN || "",
    });

    new Cdn(this, "Cdn", {
      config,
      restApi: api.restApi,
      functionUrl: invoke.functionUrl,
      invokeLambda: invoke.invokeLambda,
      a2aProxyFunctionUrl: a2aProxy.functionUrl,
      a2aProxyLambda: a2aProxy.lambda,
      originVerifyHeaderName: "x-origin-verify",
      originVerifyHeaderValue: originVerifyValue,
      webAclArn: props.webAclArn,
    });
  }
}
