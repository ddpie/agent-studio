import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as path from "path";
import * as fs from "fs";
import { Construct } from "constructs";

export interface BaseDeploymentProps {
  /** The bucket that runtimes download base zips from. */
  targetBucket: s3.IBucket | string;
  /** Directory containing deployment.zip + sub-agent-deployment.zip. */
  baseDir: string;
}

/**
 * Uploads base/deployment.zip and base/sub-agent-deployment.zip to S3.
 *
 * - deployment.zip           : slim deps for Meta-Agent (cold-start < 30s)
 * - sub-agent-deployment.zip : fat deps incl. Playwright for browser_use
 *
 * Zips are built out-of-band by scripts/build-base-zip.sh; CDK just
 * uploads them via BucketDeployment so the upload is reproducible across
 * new environments and tied to stack lifecycle.
 */
export class BaseDeployment extends Construct {
  constructor(scope: Construct, id: string, props: BaseDeploymentProps) {
    super(scope, id);

    const requiredZips = ["deployment.zip", "sub-agent-deployment.zip"];
    for (const zip of requiredZips) {
      const full = path.join(props.baseDir, zip);
      if (!fs.existsSync(full)) {
        throw new Error(
          `${full} not found. Run: bash scripts/build-base-zip.sh before cdk deploy.`,
        );
      }
    }

    const bucket =
      typeof props.targetBucket === "string"
        ? s3.Bucket.fromBucketName(this, "TargetBucket", props.targetBucket)
        : props.targetBucket;

    new s3deploy.BucketDeployment(this, "UploadBaseZips", {
      sources: [
        s3deploy.Source.asset(props.baseDir, {
          exclude: ["*", "!deployment.zip", "!sub-agent-deployment.zip"],
        }),
      ],
      destinationBucket: bucket,
      destinationKeyPrefix: "base",
      prune: false,
      retainOnDelete: true,
      // Sub-agent zip is ~100MB; the deploy Lambda's /tmp needs room
      // to stage + unzip without running out of space.
      memoryLimit: 1024,
      ephemeralStorageSize: cdk.Size.mebibytes(2048),
    });
  }
}
