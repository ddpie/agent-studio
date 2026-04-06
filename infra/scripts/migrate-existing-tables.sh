#!/usr/bin/env bash
# Add GSIs and enable PITR on existing DynamoDB tables.
# Run once during Phase 0 deployment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../../.env"

REGION="${AGENT_STUDIO_REGION}"

echo "=== Adding GSIs to agent-studio-agents ==="

# workspace-index GSI
echo "Adding workspace-index GSI..."
aws dynamodb update-table \
  --table-name agent-studio-agents \
  --region "$REGION" \
  --attribute-definitions \
    AttributeName=workspace_id,AttributeType=S \
    AttributeName=created_at,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "workspace-index",
      "KeySchema": [
        {"AttributeName": "workspace_id", "KeyType": "HASH"},
        {"AttributeName": "created_at", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"}
    }
  }]' 2>/dev/null || echo "  workspace-index may already exist, skipping."

# wait table-exists 对已存在的表会立即返回，必须轮询 TableStatus == ACTIVE
echo "Waiting for agents table to become ACTIVE..."
while true; do
  STATUS=$(aws dynamodb describe-table --table-name agent-studio-agents --region "$REGION" --query "Table.TableStatus" --output text)
  [ "$STATUS" = "ACTIVE" ] && break
  echo "  Table status: $STATUS, waiting..."
  sleep 10
done

# public-index GSI
echo "Adding public-index GSI..."
aws dynamodb update-table \
  --table-name agent-studio-agents \
  --region "$REGION" \
  --attribute-definitions \
    AttributeName=visibility,AttributeType=S \
    AttributeName=created_at,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "public-index",
      "KeySchema": [
        {"AttributeName": "visibility", "KeyType": "HASH"},
        {"AttributeName": "created_at", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"}
    }
  }]' 2>/dev/null || echo "  public-index may already exist, skipping."

echo "Waiting for agents table to become ACTIVE..."
while true; do
  STATUS=$(aws dynamodb describe-table --table-name agent-studio-agents --region "$REGION" --query "Table.TableStatus" --output text)
  [ "$STATUS" = "ACTIVE" ] && break
  echo "  Table status: $STATUS, waiting..."
  sleep 10
done

echo "=== Adding GSI to agent-studio-tools ==="

aws dynamodb update-table \
  --table-name agent-studio-tools \
  --region "$REGION" \
  --attribute-definitions \
    AttributeName=workspace_id,AttributeType=S \
    AttributeName=created_at,AttributeType=S \
  --global-secondary-index-updates '[{
    "Create": {
      "IndexName": "workspace-index",
      "KeySchema": [
        {"AttributeName": "workspace_id", "KeyType": "HASH"},
        {"AttributeName": "created_at", "KeyType": "RANGE"}
      ],
      "Projection": {"ProjectionType": "ALL"}
    }
  }]' 2>/dev/null || echo "  workspace-index may already exist, skipping."

echo "Waiting for tools table to become ACTIVE..."
while true; do
  STATUS=$(aws dynamodb describe-table --table-name agent-studio-tools --region "$REGION" --query "Table.TableStatus" --output text)
  [ "$STATUS" = "ACTIVE" ] && break
  echo "  Table status: $STATUS, waiting..."
  sleep 10
done

echo "=== Enabling PITR on existing tables ==="

aws dynamodb update-continuous-backups \
  --table-name agent-studio-agents \
  --region "$REGION" \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

aws dynamodb update-continuous-backups \
  --table-name agent-studio-tools \
  --region "$REGION" \
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true

echo "=== Done ==="
