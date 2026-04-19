#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import * as path from "path";
import { AgentStudioStack } from "../lib/agent-studio-stack";
import { WafStack } from "../lib/waf-stack";
import { getConfig } from "../lib/config";
import { PublicAccessGuard } from "../lib/aspects/public-access-guard";

dotenv.config({ path: path.resolve(__dirname, "../../.env") });
const config = getConfig();

const useExistingCognito = process.env.AGENT_STUDIO_USE_EXISTING_COGNITO === "true";
const existingMetaAgentId = process.env.AGENT_STUDIO_EXISTING_META_AGENT_ID || "";

const app = new cdk.App();

const wafStack = new WafStack(app, "AgentStudioWafStack", {
  env: { account: config.accountId, region: "us-east-1" },
  crossRegionReferences: true,
});

new AgentStudioStack(app, "AgentStudioStack", {
  config,
  webAclArn: wafStack.webAclArn,
  useExistingCognito,
  existingCognitoUserPoolId: process.env.AGENT_STUDIO_COGNITO_USER_POOL_ID,
  existingCognitoClientId: process.env.AGENT_STUDIO_COGNITO_CLIENT_ID,
  existingMetaAgentId: existingMetaAgentId || undefined,
  env: { account: config.accountId, region: config.region },
  crossRegionReferences: true,
});

// Fail synth if anyone reintroduces AuthType=NONE or Principal:"*" on Lambda.
cdk.Aspects.of(app).add(new PublicAccessGuard());
