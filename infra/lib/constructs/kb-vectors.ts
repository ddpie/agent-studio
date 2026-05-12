import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import { Construct } from "constructs";

export interface KbVectorsProps {
  region: string;
  accountId: string;
  s3Bucket: string;
}

/**
 * Infrastructure for the Knowledge Base feature:
 * - S3 Vector Bucket (stores embeddings)
 * - KB Service Role (assumed by Bedrock for ingestion + retrieval)
 */
export class KbVectors extends Construct {
  public readonly vectorBucketArn: string;
  public readonly vectorBucketName: string;
  public readonly kbServiceRoleArn: string;

  constructor(scope: Construct, id: string, props: KbVectorsProps) {
    super(scope, id);

    const { region, accountId, s3Bucket } = props;

    // ─── S3 Vector Bucket ───
    const vectorBucket = new s3vectors.CfnVectorBucket(this, "VectorBucket", {
      vectorBucketName: `studio-vectors-${accountId}-${region}`,
    });
    vectorBucket.applyRemovalPolicy(cdk.RemovalPolicy.RETAIN);

    this.vectorBucketArn = vectorBucket.attrVectorBucketArn;
    this.vectorBucketName = `studio-vectors-${accountId}-${region}`;

    // ─── KB Service Role (Bedrock assumes this for ingestion + retrieval) ───
    const kbServiceRole = new iam.Role(this, "KBServiceRole", {
      roleName: `AgentStudioKBServiceRole-${region}`,
      assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": accountId },
        },
      }),
    });

    // Read source documents from S3
    kbServiceRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3:GetObject", "s3:ListBucket"],
      resources: [
        `arn:aws:s3:::${s3Bucket}`,
        `arn:aws:s3:::${s3Bucket}/kb/*`,
      ],
    }));

    // Vector store operations — Bedrock KB engine needs full vector CRUD
    kbServiceRole.addToPolicy(new iam.PolicyStatement({
      actions: ["s3vectors:*"],
      resources: ["*"],
    }));

    // Embedding model access
    kbServiceRole.addToPolicy(new iam.PolicyStatement({
      actions: ["bedrock:InvokeModel"],
      resources: [
        `arn:aws:bedrock:${region}::foundation-model/cohere.embed-multilingual-v3`,
      ],
    }));

    this.kbServiceRoleArn = kbServiceRole.roleArn;

    new cdk.CfnOutput(this, "VectorBucketArn", { value: this.vectorBucketArn });
    new cdk.CfnOutput(this, "KBServiceRoleArn", { value: this.kbServiceRoleArn });
  }
}
