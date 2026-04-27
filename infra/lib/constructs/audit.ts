import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";

/**
 * Platform audit table.
 *
 * Captures platform-admin mutations (broadcast-upgrade, role recreate, etc.)
 * and sensitive workspace-level actions. Consumed by compliance / incident response.
 *
 * PK: AUDIT#platform-admin | AUDIT#WS#{wsId}
 * SK: ISO-8601 timestamp
 * TTL: 1 year via expires_at attribute (set at write time).
 */
export class AuditTable extends Construct {
  public readonly table: dynamodb.Table;

  constructor(scope: Construct, id: string) {
    super(scope, id);

    this.table = new dynamodb.Table(this, "Audit", {
      tableName: "agent-studio-audit",
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      timeToLiveAttribute: "expires_at",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI: query by actor across workspaces (e.g. "what did alice do this month?")
    this.table.addGlobalSecondaryIndex({
      indexName: "actor-index",
      partitionKey: { name: "actor", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    new cdk.CfnOutput(this, "AuditTableName", {
      value: this.table.tableName,
      exportName: "AgentStudio-AuditTableName",
    });
  }
}
