import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";

export class Database extends Construct {
  public readonly workspacesTable: dynamodb.Table;
  public readonly skillsTable: dynamodb.Table;
  public readonly agentsTable: dynamodb.ITable;
  public readonly toolsTable: dynamodb.ITable;

  constructor(scope: Construct, id: string, props: {
    existingAgentsTableName: string;
    existingToolsTableName: string;
  }) {
    super(scope, id);

    this.workspacesTable = new dynamodb.Table(this, "Workspaces", {
      tableName: "agent-studio-workspaces",
      partitionKey: { name: "workspaceId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
      timeToLiveAttribute: "expires_at",
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.workspacesTable.addGlobalSecondaryIndex({
      indexName: "user-index",
      partitionKey: { name: "userId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "workspaceId", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.skillsTable = new dynamodb.Table(this, "Skills", {
      tableName: "agent-studio-skills",
      partitionKey: { name: "skillId", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });
    this.skillsTable.addGlobalSecondaryIndex({
      indexName: "workspace-index",
      partitionKey: { name: "workspace_id", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "created_at", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.agentsTable = dynamodb.Table.fromTableName(this, "Agents", props.existingAgentsTableName);
    this.toolsTable = dynamodb.Table.fromTableName(this, "Tools", props.existingToolsTableName);
  }
}
