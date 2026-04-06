#!/usr/bin/env bash
# Add GSIs and enable PITR on existing DynamoDB tables.
# Run once during Phase 0 deployment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../../.env"

REGION="${AGENT_STUDIO_REGION}"

wait_gsis_active() {
  local table=$1
  echo "Waiting for all GSIs on $table to become ACTIVE..."
  while true; do
    CREATING=$(aws dynamodb describe-table --table-name "$table" --region "$REGION" \
      --query "Table.GlobalSecondaryIndexes[?IndexStatus!='ACTIVE'].IndexName" --output text)
    [ -z "$CREATING" ] && break
    echo "  Still creating: $CREATING"
    sleep 15
  done
  echo "  All GSIs ACTIVE on $table"
}

echo "=== Adding GSIs to agent-studio-agents ==="

echo "Adding workspace-index GSI..."
OUTPUT=$(aws dynamodb update-table \
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
  }]' 2>&1) || {
  if echo "$OUTPUT" | grep -q "ResourceInUseException"; then
    echo "  workspace-index already exists, skipping."
  else
    echo "ERROR: $OUTPUT" >&2; exit 1
  fi
}

wait_gsis_active agent-studio-agents

echo "Adding public-index GSI..."
OUTPUT=$(aws dynamodb update-table \
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
  }]' 2>&1) || {
  if echo "$OUTPUT" | grep -q "ResourceInUseException"; then
    echo "  public-index already exists, skipping."
  else
    echo "ERROR: $OUTPUT" >&2; exit 1
  fi
}

wait_gsis_active agent-studio-agents

echo "=== Adding GSI to agent-studio-tools ==="

OUTPUT=$(aws dynamodb update-table \
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
  }]' 2>&1) || {
  if echo "$OUTPUT" | grep -q "ResourceInUseException"; then
    echo "  workspace-index already exists, skipping."
  else
    echo "ERROR: $OUTPUT" >&2; exit 1
  fi
}

wait_gsis_active agent-studio-tools

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
