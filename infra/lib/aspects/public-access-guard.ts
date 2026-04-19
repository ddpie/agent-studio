import * as cdk from "aws-cdk-lib";
import { IConstruct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";

/**
 * Synth-time guard that fails the build if any Lambda Function URL has
 * AuthType=NONE, or any Lambda resource policy grants Principal:"*".
 *
 * Historical context (2026-04-19, commit 7802d49): a2a-proxy was
 * briefly given AuthType=NONE + Principal:"*" scoped by conditions,
 * rationalized as "CloudFront-only" via SourceArn + shared-secret
 * header. Public-access scanners don't parse conditions; they
 * pattern-match Principal:"*" and flagged it. This aspect exists so
 * no future change can land the same pattern without an explicit
 * `cdk-nag`-style suppression.
 */
export class PublicAccessGuard implements cdk.IAspect {
  public visit(node: IConstruct): void {
    if (node instanceof lambda.CfnUrl) {
      const authType = node.authType;
      if (authType === "NONE") {
        cdk.Annotations.of(node).addError(
          `[PublicAccessGuard] Lambda Function URL has AuthType=NONE. ` +
            `This is a red line — always use AWS_IAM + CloudFront OAC. ` +
            `If a downstream protocol requires Authorization: Bearer, ` +
            `add a CloudFront viewer-request Function to rename the ` +
            `header before OAC signs (see cdn.ts::A2aRenameAuthHeader).`
        );
      }
    }
    if (node instanceof lambda.CfnPermission) {
      if (node.principal === "*") {
        cdk.Annotations.of(node).addError(
          `[PublicAccessGuard] Lambda resource policy grants Principal:"*". ` +
            `Public-access scanners flag this regardless of conditions. ` +
            `Use a Service principal (e.g. cloudfront.amazonaws.com) scoped ` +
            `by SourceArn.`
        );
      }
    }
  }
}
