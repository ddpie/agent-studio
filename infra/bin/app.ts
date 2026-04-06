#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import * as path from "path";
import { AgentStudioStack } from "../lib/agent-studio-stack";
import { WafStack } from "../lib/waf-stack";
import { getConfig } from "../lib/config";

dotenv.config({ path: path.resolve(__dirname, "../../.env") });
const config = getConfig();
const app = new cdk.App();

const wafStack = new WafStack(app, "AgentStudioWafStack", {
  env: { account: config.accountId, region: "us-east-1" },
  crossRegionReferences: true,
});

new AgentStudioStack(app, "AgentStudioStack", {
  config,
  webAclArn: wafStack.webAclArn,
  env: { account: config.accountId, region: config.region },
  crossRegionReferences: true,
});
