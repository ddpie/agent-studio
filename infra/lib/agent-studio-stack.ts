import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { AgentStudioConfig } from "./config";

export class AgentStudioStack extends cdk.Stack {
  constructor(scope: Construct, id: string, config: AgentStudioConfig, props?: cdk.StackProps) {
    super(scope, id, props);
  }
}
