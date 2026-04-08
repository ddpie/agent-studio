import * as cdk from "aws-cdk-lib";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { Construct } from "constructs";

export class WafStack extends cdk.Stack {
  public readonly webAclArn: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const webAcl = new wafv2.CfnWebACL(this, "WebAcl", {
      defaultAction: { allow: {} },
      scope: "CLOUDFRONT",
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: "agent-studio-waf",
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: "AWSManagedRulesCommonRuleSet",
          priority: 0,
          overrideAction: { none: {} },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: "common-rules",
            sampledRequestsEnabled: true,
          },
          statement: {
            managedRuleGroupStatement: {
              vendorName: "AWS",
              name: "AWSManagedRulesCommonRuleSet",
              excludedRules: [
                // /api/* paths carry JWT auth; extension checks
                // block legitimate .txt/.py file endpoints in both
                // URI path and query params (?path=system_prompt.txt)
                { name: "RestrictedExtensions_URIPATH" },
                { name: "RestrictedExtensions_QUERYARGUMENTS" },
                // Skill import can have large body (multi-file skills from GitHub)
                // API Gateway has its own 10MB limit; JWT auth protects the endpoint
                { name: "SizeRestrictions_Body" },
              ],
            },
          },
        },
        {
          name: "RateLimit",
          priority: 1,
          action: { block: {} },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: "rate-limit",
            sampledRequestsEnabled: true,
          },
          statement: {
            rateBasedStatement: { limit: 2000, aggregateKeyType: "IP" },
          },
        },
      ],
    });

    this.webAclArn = webAcl.attrArn;

    new cdk.CfnOutput(this, "WebAclArn", {
      value: webAcl.attrArn,
      exportName: "AgentStudioWafAclArn",
    });
  }
}
