import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as path from "path";
import * as fs from "fs";
import { Construct } from "constructs";

export interface BaseDeploymentProps {
  /** The bucket that sub-agents download base/deployment.zip from. */
  targetBucket: s3.IBucket | string;
  /** Absolute path to the pre-built deployment.zip on disk. */
  zipPath: string;
}

/**
 * Uploads base/deployment.zip (the shared sub-agent dependency layer)
 * to s3://{bucket}/base/deployment.zip.
 *
 * The zip is NOT built by CDK — run `bash scripts/build-base-zip.sh`
 * first. CDK only manages the upload so the artifact is reproducible
 * across deployments and ties to stack lifecycle.
 */
export class BaseDeployment extends Construct {
  constructor(scope: Construct, id: string, props: BaseDeploymentProps) {
    super(scope, id);

    if (!fs.existsSync(props.zipPath)) {
      throw new Error(
        `base/deployment.zip not found at ${props.zipPath}. ` +
          `Run: bash scripts/build-base-zip.sh before cdk deploy.`,
      );
    }

    const bucket =
      typeof props.targetBucket === "string"
        ? s3.Bucket.fromBucketName(this, "TargetBucket", props.targetBucket)
        : props.targetBucket;

    new s3deploy.BucketDeployment(this, "UploadBaseZip", {
      sources: [
        s3deploy.Source.asset(path.dirname(props.zipPath), {
          // Only include deployment.zip from the directory
          exclude: ["*", "!deployment.zip"],
        }),
      ],
      destinationBucket: bucket,
      destinationKeyPrefix: "base",
      prune: false,
      retainOnDelete: true,
      memoryLimit: 512,
    });
  }
}
