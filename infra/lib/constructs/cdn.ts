import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { AgentStudioConfig } from "../config";

export interface CdnProps {
  config: AgentStudioConfig;
  restApi: apigateway.RestApi;
  functionUrl: lambda.FunctionUrl;
  invokeLambda: lambda.Function;
  originVerifyHeaderName: string;
  originVerifyHeaderValue: string;
  webAclArn: string;
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

    // API Gateway origin
    const apiOrigin = new origins.RestApiOrigin(props.restApi, {
      customHeaders: {
        [props.originVerifyHeaderName]: props.originVerifyHeaderValue,
      },
    });

    // Lambda Function URL origin with OAC (IAM auth, CloudFront signs requests via SigV4)
    const invokeOrigin = origins.FunctionUrlOrigin.withOriginAccessControl(props.functionUrl);

    // CloudFront OAC requires both InvokeFunctionUrl AND InvokeFunction permissions
    // CDK auto-adds InvokeFunctionUrl but not InvokeFunction
    props.invokeLambda.addPermission("CloudFrontInvokeFunction", {
      principal: new cdk.aws_iam.ServicePrincipal("cloudfront.amazonaws.com"),
      action: "lambda:InvokeFunction",
      sourceArn: `arn:aws:cloudfront::${props.config.accountId}:distribution/*`,
    });

    // CORS response headers policy for API (开发时浏览器直连 CloudFront)
    const corsPolicy = new cloudfront.ResponseHeadersPolicy(this, "ApiCorsPolicy", {
      responseHeadersPolicyName: "agent-studio-api-cors",
      corsBehavior: {
        accessControlAllowOrigins: ["*"],
        accessControlAllowHeaders: ["Authorization", "Content-Type"],
        accessControlAllowMethods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        accessControlAllowCredentials: false,
        accessControlMaxAge: cdk.Duration.seconds(3600),
        originOverride: true,
      },
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
          responseHeadersPolicy: corsPolicy,
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
      webAclId: props.webAclArn,
    });

    new cdk.CfnOutput(this, "CloudFrontDomain", { value: this.distribution.distributionDomainName });
    new cdk.CfnOutput(this, "CloudFrontId", { value: this.distribution.distributionId });
    new cdk.CfnOutput(this, "FrontendBucketName", { value: this.frontendBucket.bucketName });

    // S3 CORS for assets bucket (external, not CDK-managed)
    // Needed for presigned URL downloads from browser
    new cr.AwsCustomResource(this, "AssetsBucketCors", {
      onCreate: {
        service: "S3",
        action: "putBucketCors",
        parameters: {
          Bucket: props.config.s3Bucket,
          CORSConfiguration: {
            CORSRules: [{
              AllowedOrigins: [
                `https://${this.distribution.distributionDomainName}`,
                "http://localhost:5173",
                "http://localhost:5174",
              ],
              AllowedMethods: ["GET"],
              AllowedHeaders: ["*"],
              MaxAgeSeconds: 3600,
            }],
          },
        },
        physicalResourceId: cr.PhysicalResourceId.of("assets-bucket-cors"),
      },
      onUpdate: {
        service: "S3",
        action: "putBucketCors",
        parameters: {
          Bucket: props.config.s3Bucket,
          CORSConfiguration: {
            CORSRules: [{
              AllowedOrigins: [
                `https://${this.distribution.distributionDomainName}`,
                "http://localhost:5173",
                "http://localhost:5174",
              ],
              AllowedMethods: ["GET"],
              AllowedHeaders: ["*"],
              MaxAgeSeconds: 3600,
            }],
          },
        },
        physicalResourceId: cr.PhysicalResourceId.of("assets-bucket-cors"),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: [`arn:aws:s3:::${props.config.s3Bucket}`],
      }),
    });
  }
}
