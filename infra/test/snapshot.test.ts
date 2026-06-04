/**
 * CDK Snapshot + Security Invariant Tests for AgentStudioStack.
 *
 * These tests synthesize the main stack with test props and assert:
 * 1. The CloudFormation template matches a stored snapshot (drift detection)
 * 2. Critical security invariants hold:
 *    - All Lambda Function URLs use AuthType=AWS_IAM
 *    - No Lambda resource policy has Principal="*"
 *    - CDK-managed DynamoDB tables have PointInTimeRecoveryEnabled
 *    - S3 buckets have PublicAccessBlockConfiguration
 *
 * NOTE: cdk-nag is NOT in devDependencies. Consider adding it for
 * rule-pack assertions (e.g., AwsSolutions, NIST 800-53).
 * Install: npm i -D cdk-nag
 */
import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { AgentStudioStack } from "../lib/agent-studio-stack";

// ─── Test constants ───
const TEST_ACCOUNT = "123456789012";
const TEST_REGION = "us-east-1";
const TEST_S3_BUCKET = "test-bucket-123456789012-us-east-1";

let app: cdk.App;
let stack: AgentStudioStack;
let template: Template;

beforeAll(() => {
  // McpRoles reads mcp-registry.yaml at synth time — it exists in the repo.

  // Set required env vars that the stack reads directly
  process.env.ORIGIN_VERIFY_SECRET = "test-secret-0123456789abcdef0123456789abcdef";
  process.env.AGENT_STUDIO_CLOUDFRONT_DOMAIN = "test.cloudfront.net";

  // Skip Docker bundling in tests — CDK replaces code with a placeholder asset.
  // Without this, Docker bundling produces non-deterministic hashes across runs,
  // breaking snapshot stability. The context key "aws:cdk:bundling-stacks"
  // controls which stacks get bundled; an empty array skips all.
  app = new cdk.App({
    context: {
      "aws:cdk:bundling-stacks": [],
    },
  });

  // The WafStack provides a webAclArn. For testing we just use a synthetic ARN.
  const webAclArn = `arn:aws:wafv2:us-east-1:${TEST_ACCOUNT}:global/webacl/test-acl/test-id`;

  stack = new AgentStudioStack(app, "TestAgentStudioStack", {
    config: {
      region: TEST_REGION,
      accountId: TEST_ACCOUNT,
      s3Bucket: TEST_S3_BUCKET,
      mcpGatewayUrl: "https://mcp-gateway.test.example.com",
    },
    webAclArn,
    useExistingCognito: false,
    existingMetaAgentId: "existing-meta-agent-runtime-id",
    env: { account: TEST_ACCOUNT, region: TEST_REGION },
  });

  template = Template.fromStack(stack);
});

// ─────────────────────────────────────────────────────────────────────────────
// Snapshot Test
// ─────────────────────────────────────────────────────────────────────────────

describe("Snapshot", () => {
  test("CloudFormation template matches snapshot", () => {
    // toJSON() gives a deterministic representation for snapshot comparison.
    // If the template changes, run: npx jest --updateSnapshot
    expect(template.toJSON()).toMatchSnapshot();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Security Invariants
// ─────────────────────────────────────────────────────────────────────────────

describe("Security: Lambda Function URLs", () => {
  test("all Function URLs have AuthType=AWS_IAM", () => {
    // Every AWS::Lambda::Url resource must set AuthType to AWS_IAM.
    // AuthType=NONE is a RED LINE per project security rules.
    const urls = template.findResources("AWS::Lambda::Url");
    const urlKeys = Object.keys(urls);

    expect(urlKeys.length).toBeGreaterThan(0); // Sanity: we have Function URLs

    for (const logicalId of urlKeys) {
      const properties = urls[logicalId].Properties;
      expect(properties.AuthType).toBe("AWS_IAM");
    }
  });
});

describe("Security: Lambda Resource Policies", () => {
  test("no Lambda permission has Principal=*", () => {
    // Principal:"*" on Lambda resource policies is flagged by scanners
    // regardless of conditions. Must use a Service principal with SourceArn.
    const permissions = template.findResources("AWS::Lambda::Permission");
    const permKeys = Object.keys(permissions);

    for (const logicalId of permKeys) {
      const properties = permissions[logicalId].Properties;
      const principal = properties.Principal;

      // Principal can be a string literal or a Fn::Join / Ref
      if (typeof principal === "string") {
        expect(principal).not.toBe("*");
      }
      // If it's an intrinsic function (Ref, Fn::GetAtt, etc), it's not "*"
    }
  });
});

describe("Security: DynamoDB Point-in-Time Recovery", () => {
  test("CDK-managed tables have PointInTimeRecovery enabled", () => {
    // Tables created by CDK (not imported) should have PITR for data protection.
    const tables = template.findResources("AWS::DynamoDB::Table");
    const tableKeys = Object.keys(tables);

    expect(tableKeys.length).toBeGreaterThan(0); // Sanity: we have tables

    // Collect tables that are CDK-managed (have explicit table names we know)
    const managedTableNames = [
      "agent-studio-workspaces",
      "agent-studio-skills",
      "agent-studio-a2a-keys",
      "agent-studio-runs",
      "agent-studio-knowledge-bases",
      "agent-studio-channels",
    ];

    for (const logicalId of tableKeys) {
      const properties = tables[logicalId].Properties;
      const tableName = properties.TableName;

      if (tableName && managedTableNames.includes(tableName)) {
        expect(properties.PointInTimeRecoverySpecification).toBeDefined();
        expect(
          properties.PointInTimeRecoverySpecification.PointInTimeRecoveryEnabled
        ).toBe(true);
      }
    }
  });

  test("at least the core tables exist", () => {
    // Verify that the expected CDK-managed tables are present
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "agent-studio-workspaces",
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });

    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "agent-studio-skills",
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });

    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "agent-studio-a2a-keys",
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });

    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "agent-studio-runs",
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });

    template.hasResourceProperties("AWS::DynamoDB::Table", {
      TableName: "agent-studio-knowledge-bases",
      PointInTimeRecoverySpecification: {
        PointInTimeRecoveryEnabled: true,
      },
    });
  });
});

describe("Security: S3 Public Access Block", () => {
  test("all S3 buckets have PublicAccessBlockConfiguration", () => {
    // Every S3 bucket provisioned by CDK must block public access.
    const buckets = template.findResources("AWS::S3::Bucket");
    const bucketKeys = Object.keys(buckets);

    expect(bucketKeys.length).toBeGreaterThan(0); // Sanity: we have buckets

    for (const logicalId of bucketKeys) {
      const properties = buckets[logicalId].Properties;
      expect(properties.PublicAccessBlockConfiguration).toBeDefined();
      expect(
        properties.PublicAccessBlockConfiguration.BlockPublicAcls
      ).toBe(true);
      expect(
        properties.PublicAccessBlockConfiguration.BlockPublicPolicy
      ).toBe(true);
      expect(
        properties.PublicAccessBlockConfiguration.IgnorePublicAcls
      ).toBe(true);
      expect(
        properties.PublicAccessBlockConfiguration.RestrictPublicBuckets
      ).toBe(true);
    }
  });

  test("frontend bucket uses BLOCK_ALL", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      BucketName: `agent-studio-frontend-${TEST_ACCOUNT}-${TEST_REGION}`,
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Structural Assertions (non-security, for regression detection)
// ─────────────────────────────────────────────────────────────────────────────

describe("Structure: Lambda Functions", () => {
  test("CRUD Lambda exists with Python 3.12", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "agent-studio-crud",
      Runtime: "python3.12",
    });
  });

  test("Invoke Lambda exists with Node.js 22", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "agent-studio-invoke",
      Runtime: "nodejs22.x",
    });
  });

  test("A2A Proxy Lambda exists with Node.js 22", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "agent-studio-a2a-proxy",
      Runtime: "nodejs22.x",
    });
  });

  test("Schedule Runner Lambda exists", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      FunctionName: "agent-studio-schedule-runner",
      Runtime: "nodejs22.x",
    });
  });
});

describe("Structure: CloudFront", () => {
  test("distribution exists with WAF association", () => {
    template.hasResourceProperties("AWS::CloudFront::Distribution", {
      DistributionConfig: Match.objectLike({
        WebACLId: `arn:aws:wafv2:us-east-1:${TEST_ACCOUNT}:global/webacl/test-acl/test-id`,
      }),
    });
  });
});

describe("Structure: Cognito", () => {
  test("user pool exists when not using existing Cognito", () => {
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      UserPoolName: "agent-studio-users",
    });
  });
});
