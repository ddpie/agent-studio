import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface CdnProps {
  config: AgentStudioConfig;
  restApi: apigateway.RestApi;
  functionUrl: lambda.FunctionUrl;
  originVerifyHeaderName: string;
  originVerifyHeaderValue: string;
}

export class Cdn extends Construct {
  public readonly distribution: cloudfront.Distribution;
  public readonly frontendBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: CdnProps) {
    super(scope, id);

    // 创建 S3 bucket（CDK 管理，可自动配置 OAC bucket policy）
    this.frontendBucket = new s3.Bucket(this, "FrontendBucket", {
      bucketName: `agent-studio-frontend-${props.config.accountId}-${props.config.region}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // WAF WebACL — 必须在 us-east-1（CloudFront scope 要求）
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

    // API Gateway origin
    const apiOrigin = new origins.RestApiOrigin(props.restApi, {
      customHeaders: {
        [props.originVerifyHeaderName]: props.originVerifyHeaderValue,
      },
    });

    // Lambda Function URL origin
    const fnUrlDomain = cdk.Fn.select(2, cdk.Fn.split("/", props.functionUrl.url));
    const invokeOrigin = new origins.HttpOrigin(fnUrlDomain, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      readTimeout: cdk.Duration.seconds(180),
      keepaliveTimeout: cdk.Duration.seconds(60),
    });

    // CloudFront distribution
    this.distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.frontendBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        "/api/*": {
          origin: apiOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
        "/invoke/*": {
          origin: invokeOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          compress: false, // 禁用压缩，否则 gzip 会缓冲 SSE
        },
      },
      defaultRootObject: "index.html",
      errorResponses: [
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
      webAclId: webAcl.attrArn,
    });

    new cdk.CfnOutput(this, "CloudFrontDomain", { value: this.distribution.distributionDomainName });
    new cdk.CfnOutput(this, "CloudFrontId", { value: this.distribution.distributionId });
    new cdk.CfnOutput(this, "FrontendBucketName", { value: this.frontendBucket.bucketName });
  }
}
