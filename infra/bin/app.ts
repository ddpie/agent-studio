#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import * as dotenv from "dotenv";
import * as path from "path";
import { AgentStudioStack } from "../lib/agent-studio-stack";
import { getConfig } from "../lib/config";

dotenv.config({ path: path.resolve(__dirname, "../../.env") });
const config = getConfig();
const app = new cdk.App();
new AgentStudioStack(app, "AgentStudioStack", config, {
  env: { account: config.accountId, region: config.region },
});
