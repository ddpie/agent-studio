#!/usr/bin/env python3
"""Generate backend + frontend IAM policy registries from mcp-registry.yaml.

Single source of truth: mcp-runtime/mcp-registry.yaml
Outputs:
  - lambda/crud/mcp_iam_registry.py  (Python dict, consumed by workspace_iam.py)
  - frontend/src/generated/mcp-targets.ts  (TypeScript array, consumed by IamPermissionsTab)

Usage:
  python scripts/sync-mcp-iam-policies.py          # generate files
  python scripts/sync-mcp-iam-policies.py --check   # CI mode: exit 1 if files are stale
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "mcp-runtime" / "mcp-registry.yaml"
BACKEND_OUT = ROOT / "lambda" / "crud" / "mcp_iam_registry.py"
FRONTEND_OUT = ROOT / "frontend" / "src" / "generated" / "mcp-targets.ts"

HEADER_BACKEND = '''"""Auto-generated MCP IAM policy registry — DO NOT EDIT.

Source of truth: mcp-runtime/mcp-registry.yaml
Regenerate:     python scripts/sync-mcp-iam-policies.py
"""
# SYNC_HASH: {hash}
'''

HEADER_FRONTEND = '''// Auto-generated MCP target definitions — DO NOT EDIT.
//
// Source of truth: mcp-runtime/mcp-registry.yaml
// Regenerate:     python scripts/sync-mcp-iam-policies.py
//
// SYNC_HASH: {hash}
'''

# Virtual targets: workspace-role grants that map to an AWS service not backed
# by an MCP server. These appear in the grant UI alongside real MCP targets.
# When the YAML adds a target that overlaps, the virtual entry should be removed.
VIRTUAL_TARGETS: dict[str, dict] = {
    "ec2": {
        "displayName": "EC2",
        "category": "compute",
        "policy": {
            "Statement": [{"Sid": "Ec2", "Effect": "Allow", "Action": ["ec2:Describe*"], "Resource": "*"}]
        },
    },
    "rds": {
        "displayName": "RDS",
        "category": "database",
        "policy": {
            "Statement": [{"Sid": "Rds", "Effect": "Allow", "Action": ["rds:Describe*", "rds:List*"], "Resource": "*"}]
        },
    },
    "s3-readonly": {
        "displayName": "S3 (Read-Only)",
        "category": "storage",
        "policy": {
            "Statement": [{"Sid": "S3ReadOnly", "Effect": "Allow", "Action": ["s3:GetBucketLocation", "s3:GetBucketTagging", "s3:ListAllMyBuckets", "s3:ListBucket"], "Resource": "*"}]
        },
    },
    "dynamodb-readonly": {
        "displayName": "DynamoDB (Read-Only)",
        "category": "database",
        "policy": {
            "Statement": [{"Sid": "DynamoDBReadOnly", "Effect": "Allow", "Action": ["dynamodb:Describe*", "dynamodb:List*"], "Resource": "*"}]
        },
    },
    "sns": {
        "displayName": "SNS",
        "category": "messaging",
        "policy": {
            "Statement": [{"Sid": "Sns", "Effect": "Allow", "Action": ["sns:Get*", "sns:List*"], "Resource": "*"}]
        },
    },
    "sqs": {
        "displayName": "SQS",
        "category": "messaging",
        "policy": {
            "Statement": [{"Sid": "Sqs", "Effect": "Allow", "Action": ["sqs:Get*", "sqs:List*"], "Resource": "*"}]
        },
    },
    "route53": {
        "displayName": "Route 53",
        "category": "networking",
        "policy": {
            "Statement": [{"Sid": "Route53", "Effect": "Allow", "Action": ["route53:Get*", "route53:List*"], "Resource": "*"}]
        },
    },
    "elb": {
        "displayName": "Elastic Load Balancing",
        "category": "networking",
        "policy": {
            "Statement": [{"Sid": "Elb", "Effect": "Allow", "Action": ["elasticloadbalancing:Describe*"], "Resource": "*"}]
        },
    },
    "api-gateway": {
        "displayName": "API Gateway",
        "category": "networking",
        "policy": {
            "Statement": [{"Sid": "ApiGateway", "Effect": "Allow", "Action": ["apigateway:GET"], "Resource": "*"}]
        },
    },
    "cloudfront": {
        "displayName": "CloudFront",
        "category": "networking",
        "policy": {
            "Statement": [{"Sid": "CloudFront", "Effect": "Allow", "Action": ["cloudfront:Get*", "cloudfront:List*"], "Resource": "*"}]
        },
    },
    "kms": {
        "displayName": "KMS",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Kms", "Effect": "Allow", "Action": ["kms:Describe*", "kms:List*", "kms:Get*"], "Resource": "*"}]
        },
    },
    "acm": {
        "displayName": "ACM (Certificates)",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Acm", "Effect": "Allow", "Action": ["acm:Describe*", "acm:List*", "acm:GetCertificate"], "Resource": "*"}]
        },
    },
    "guardduty": {
        "displayName": "GuardDuty",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "GuardDuty", "Effect": "Allow", "Action": ["guardduty:Get*", "guardduty:List*"], "Resource": "*"}]
        },
    },
    "security-hub": {
        "displayName": "Security Hub",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "SecurityHub", "Effect": "Allow", "Action": ["securityhub:Get*", "securityhub:List*", "securityhub:BatchGet*"], "Resource": "*"}]
        },
    },
    "inspector": {
        "displayName": "Inspector",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Inspector", "Effect": "Allow", "Action": ["inspector2:Get*", "inspector2:List*", "inspector2:BatchGet*"], "Resource": "*"}]
        },
    },
    "config": {
        "displayName": "AWS Config",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Config", "Effect": "Allow", "Action": ["config:Describe*", "config:Get*", "config:List*"], "Resource": "*"}]
        },
    },
    "sts": {
        "displayName": "STS (Identity)",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Sts", "Effect": "Allow", "Action": ["sts:GetCallerIdentity"], "Resource": "*"}]
        },
    },
    "autoscaling": {
        "displayName": "Auto Scaling",
        "category": "compute",
        "policy": {
            "Statement": [{"Sid": "AutoScaling", "Effect": "Allow", "Action": ["autoscaling:Describe*"], "Resource": "*"}]
        },
    },
    "efs": {
        "displayName": "EFS",
        "category": "storage",
        "policy": {
            "Statement": [{"Sid": "Efs", "Effect": "Allow", "Action": ["elasticfilesystem:Describe*"], "Resource": "*"}]
        },
    },
    "opensearch": {
        "displayName": "OpenSearch",
        "category": "database",
        "policy": {
            "Statement": [{"Sid": "OpenSearch", "Effect": "Allow", "Action": ["es:Describe*", "es:List*"], "Resource": "*"}]
        },
    },
    "eventbridge": {
        "displayName": "EventBridge",
        "category": "messaging",
        "policy": {
            "Statement": [{"Sid": "EventBridge", "Effect": "Allow", "Action": ["events:Describe*", "events:List*"], "Resource": "*"}]
        },
    },
    "cloudformation": {
        "displayName": "CloudFormation",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "CloudFormation", "Effect": "Allow", "Action": ["cloudformation:Describe*", "cloudformation:List*", "cloudformation:GetTemplateSummary"], "Resource": "*"}]
        },
    },
    "ssm": {
        "displayName": "Systems Manager",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "Ssm", "Effect": "Allow", "Action": ["ssm:DescribeParameters", "ssm:GetParameter", "ssm:GetParameters", "ssm:List*"], "Resource": "*"}]
        },
    },
    "service-quotas": {
        "displayName": "Service Quotas",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "ServiceQuotas", "Effect": "Allow", "Action": ["servicequotas:Get*", "servicequotas:List*"], "Resource": "*"}]
        },
    },
    "health": {
        "displayName": "AWS Health",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "Health", "Effect": "Allow", "Action": ["health:Describe*"], "Resource": "*"}]
        },
    },
    "compute-optimizer": {
        "displayName": "Compute Optimizer",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "ComputeOptimizer", "Effect": "Allow", "Action": ["compute-optimizer:Get*"], "Resource": "*"}]
        },
    },
    "cost-explorer": {
        "displayName": "Cost Explorer",
        "category": "cost",
        "policy": {
            "Statement": [{"Sid": "CostExplorer", "Effect": "Allow", "Action": ["ce:Get*", "ce:Describe*", "ce:List*"], "Resource": "*"}]
        },
    },
    "athena": {
        "displayName": "Athena",
        "category": "analytics",
        "policy": {
            "Statement": [{"Sid": "Athena", "Effect": "Allow", "Action": ["athena:Get*", "athena:List*", "athena:BatchGet*"], "Resource": "*"}]
        },
    },
    "kinesis": {
        "displayName": "Kinesis",
        "category": "analytics",
        "policy": {
            "Statement": [{"Sid": "Kinesis", "Effect": "Allow", "Action": ["kinesis:Describe*", "kinesis:List*", "kinesis:Get*"], "Resource": "*"}]
        },
    },
    "cognito": {
        "displayName": "Cognito",
        "category": "security",
        "policy": {
            "Statement": [{"Sid": "Cognito", "Effect": "Allow", "Action": ["cognito-idp:Describe*", "cognito-idp:List*"], "Resource": "*"}]
        },
    },
    "backup": {
        "displayName": "AWS Backup",
        "category": "management",
        "policy": {
            "Statement": [{"Sid": "Backup", "Effect": "Allow", "Action": ["backup:Describe*", "backup:Get*", "backup:List*"], "Resource": "*"}]
        },
    },
    "ecr": {
        "displayName": "ECR",
        "category": "compute",
        "policy": {
            "Statement": [{"Sid": "Ecr", "Effect": "Allow", "Action": ["ecr:Describe*", "ecr:List*", "ecr:BatchGetImage"], "Resource": "*"}]
        },
    },
    "bedrock-readonly": {
        "displayName": "Bedrock (Read-Only)",
        "category": "ai_ml",
        "policy": {
            "Statement": [{"Sid": "BedrockReadOnly", "Effect": "Allow", "Action": ["bedrock:Get*", "bedrock:List*"], "Resource": "*"}]
        },
    },
    "sagemaker": {
        "displayName": "SageMaker",
        "category": "ai_ml",
        "policy": {
            "Statement": [{"Sid": "SageMaker", "Effect": "Allow", "Action": ["sagemaker:Describe*", "sagemaker:List*"], "Resource": "*"}]
        },
    },
    "glue": {
        "displayName": "Glue",
        "category": "analytics",
        "policy": {
            "Statement": [{"Sid": "Glue", "Effect": "Allow", "Action": ["glue:Get*", "glue:List*", "glue:BatchGet*"], "Resource": "*"}]
        },
    },
    "redshift": {
        "displayName": "Redshift",
        "category": "database",
        "policy": {
            "Statement": [{"Sid": "Redshift", "Effect": "Allow", "Action": ["redshift:Describe*", "redshift:List*"], "Resource": "*"}]
        },
    },
}

# Name overrides: YAML names that map to friendlier display names
DISPLAY_NAME_OVERRIDES = {
    "aws-api": "AWS API",
    "aws-knowledge": "AWS Knowledge",
    "aws-pricing": "AWS Pricing",
    "billing-cost-management": "Billing & Cost Management",
    "cloudwatch": "CloudWatch",
    "cloudwatch-applicationsignals": "CloudWatch Application Signals",
    "cloudtrail": "CloudTrail",
    "iam": "IAM",
    "well-architected-security": "Well-Architected Security",
    "ecs": "ECS",
    "eks": "EKS",
    "lambda-tool": "Lambda",
    "stepfunctions-tool": "Step Functions",
    "s3-tables": "S3 Tables",
    "dynamodb": "DynamoDB",
    "aurora-dsql": "Aurora DSQL",
    "neptune": "Neptune",
    "postgres": "PostgreSQL (RDS)",
    "mysql": "MySQL (RDS)",
    "documentdb": "DocumentDB",
    "elasticache": "ElastiCache",
    "valkey": "Valkey",
    "memcached": "Memcached",
    "timestream-for-influxdb": "Timestream",
    "sns-sqs": "SNS + SQS",
    "amazon-mq": "Amazon MQ",
    "msk": "MSK (Kafka)",
    "bedrock-kb-retrieval": "Bedrock Knowledge Base",
    "sagemaker-ai": "SageMaker AI",
    "bedrock-data-automation": "Bedrock Data Automation",
    "bedrock-agentcore": "Bedrock AgentCore",
    "kendra-index": "Kendra",
    "qindex": "Amazon Q Index",
    "qbusiness-anonymous": "Amazon Q Business",
    "network": "VPC Network",
    "appsync": "AppSync",
    "prometheus": "Prometheus",
    "healthomics": "HealthOmics",
    "healthlake": "HealthLake",
    "iot-sitewise": "IoT SiteWise",
    "location": "Location Service",
    "dataprocessing": "Data Processing (Glue)",
    "syntheticdata": "Synthetic Data",
    "diagram": "Architecture Diagrams",
    "code-doc-gen": "Code Documentation",
    "openapi": "OpenAPI",
    "support": "AWS Support",
}

# Category display order
CATEGORY_ORDER = [
    "general",
    "observability",
    "security",
    "cost",
    "compute",
    "storage",
    "database",
    "networking",
    "messaging",
    "search",
    "analytics",
    "data",
    "ai_ml",
    "industry",
    "devtools",
    "operations",
    "management",
]


def _to_sid(name: str) -> str:
    """Convert target name to a valid IAM Sid: PascalCase, alphanum only."""
    return re.sub(r"[^a-zA-Z0-9]", "", name.title().replace("-", " ").title().replace(" ", ""))


def _load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return yaml.safe_load(f)


def _extract_targets(registry: dict) -> list[dict]:
    """Extract all targets with their IAM policies from the YAML."""
    targets = []

    for remote in registry.get("remote_targets", []):
        targets.append({
            "name": remote["name"],
            "displayName": DISPLAY_NAME_OVERRIDES.get(remote["name"], remote["name"]),
            "category": remote.get("category", "general"),
            "enabled": remote.get("enabled", True),
            "iamPolicy": remote.get("iam_policy"),
            "source": "yaml",
        })

    for rt in registry.get("runtime_targets", []):
        policy = rt.get("iam_policy")
        if policy:
            for stmt in policy.get("Statement", []):
                if "Sid" not in stmt:
                    stmt["Sid"] = _to_sid(rt["name"])
        targets.append({
            "name": rt["name"],
            "displayName": DISPLAY_NAME_OVERRIDES.get(rt["name"], rt["name"]),
            "category": rt.get("category", "general"),
            "enabled": rt.get("enabled", True),
            "iamPolicy": policy,
            "source": "yaml",
        })

    # Add virtual targets (not backed by MCP servers)
    yaml_names = {t["name"] for t in targets}
    for name, vt in VIRTUAL_TARGETS.items():
        if name not in yaml_names:
            targets.append({
                "name": name,
                "displayName": vt["displayName"],
                "category": vt["category"],
                "enabled": True,
                "iamPolicy": vt["policy"],
                "source": "virtual",
            })

    # Sort by category order, then by name within category
    def sort_key(t):
        cat_idx = CATEGORY_ORDER.index(t["category"]) if t["category"] in CATEGORY_ORDER else 999
        return (cat_idx, t["name"])

    targets.sort(key=sort_key)
    return targets


def _compute_hash(targets: list[dict]) -> str:
    """Deterministic hash of target policies for staleness detection."""
    serialized = json.dumps(
        [{k: t[k] for k in ("name", "iamPolicy")} for t in targets],
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


def _generate_backend(targets: list[dict], sync_hash: str) -> str:
    """Generate Python source for MCP_IAM_POLICIES and _NO_IAM_TARGETS."""
    lines = [HEADER_BACKEND.format(hash=sync_hash)]
    lines.append("")
    lines.append("MCP_IAM_POLICIES: dict[str, dict | None] = {")

    for t in targets:
        if t["iamPolicy"] is None:
            continue
        policy_json = json.dumps(t["iamPolicy"], indent=4)
        # Indent by 4 for dict nesting
        indented = "\n".join("    " + line if line.strip() else line for line in policy_json.split("\n"))
        lines.append(f'    "{t["name"]}": {indented.strip()},')

    lines.append("}")
    lines.append("")
    lines.append("# Targets that exist but require no IAM permissions.")
    no_iam = sorted(t["name"] for t in targets if t["iamPolicy"] is None)
    lines.append(f'_NO_IAM_TARGETS = {{{", ".join(repr(n) for n in no_iam)}}}')
    lines.append("")
    return "\n".join(lines)


def _generate_frontend(targets: list[dict], sync_hash: str) -> str:
    """Generate TypeScript source for MCP_TARGETS array."""
    lines = [HEADER_FRONTEND.format(hash=sync_hash)]
    lines.append("")
    lines.append("export interface McpTargetDef {")
    lines.append("  name: string;")
    lines.append("  displayName: string;")
    lines.append("  category: string;")
    lines.append("  iamPolicy: { Statement: { Sid: string; Effect: string; Action: string[]; Resource: string }[] } | null;")
    lines.append("}")
    lines.append("")
    lines.append("export const MCP_TARGETS: McpTargetDef[] = [")

    for t in targets:
        policy_ts = "null"
        if t["iamPolicy"]:
            stmts = t["iamPolicy"].get("Statement", [])
            stmt_parts = []
            for s in stmts:
                actions = ", ".join(f'"{a}"' for a in s["Action"])
                stmt_parts.append(
                    f'{{ Sid: "{s.get("Sid", "")}", Effect: "{s["Effect"]}", '
                    f'Action: [{actions}], Resource: "{s["Resource"]}" }}'
                )
            policy_ts = "{ Statement: [" + ", ".join(stmt_parts) + "] }"

        lines.append(f'  {{ name: "{t["name"]}", displayName: "{t["displayName"]}", '
                     f'category: "{t["category"]}", iamPolicy: {policy_ts} }},')

    lines.append("];")
    lines.append("")
    return "\n".join(lines)


def _read_existing_hash(path: Path) -> str | None:
    """Extract SYNC_HASH from an existing generated file."""
    if not path.exists():
        return None
    content = path.read_text()
    m = re.search(r"SYNC_HASH:\s+([a-f0-9]+)", content)
    return m.group(1) if m else None


def main():
    parser = argparse.ArgumentParser(description="Sync MCP IAM policies from registry YAML")
    parser.add_argument("--check", action="store_true", help="Check mode: exit 1 if files are stale")
    args = parser.parse_args()

    registry = _load_registry()
    targets = _extract_targets(registry)
    sync_hash = _compute_hash(targets)

    if args.check:
        be_hash = _read_existing_hash(BACKEND_OUT)
        fe_hash = _read_existing_hash(FRONTEND_OUT)
        stale = []
        if be_hash != sync_hash:
            stale.append(f"  backend: expected {sync_hash}, got {be_hash or 'missing'}")
        if fe_hash != sync_hash:
            stale.append(f"  frontend: expected {sync_hash}, got {fe_hash or 'missing'}")
        if stale:
            print(f"ERROR: MCP IAM policy registries are stale!\n" + "\n".join(stale))
            print(f"\nRun: python scripts/sync-mcp-iam-policies.py")
            sys.exit(1)
        print(f"OK: registries are up to date (hash: {sync_hash})")
        sys.exit(0)

    backend_src = _generate_backend(targets, sync_hash)
    frontend_src = _generate_frontend(targets, sync_hash)

    BACKEND_OUT.write_text(backend_src)
    FRONTEND_OUT.write_text(frontend_src)

    # Stats
    with_iam = sum(1 for t in targets if t["iamPolicy"])
    no_iam = sum(1 for t in targets if t["iamPolicy"] is None)
    from_yaml = sum(1 for t in targets if t["source"] == "yaml")
    from_virtual = sum(1 for t in targets if t["source"] == "virtual")
    print(f"Synced {len(targets)} targets ({from_yaml} from YAML, {from_virtual} virtual)")
    print(f"  {with_iam} with IAM policies, {no_iam} without")
    print(f"  Backend:  {BACKEND_OUT}")
    print(f"  Frontend: {FRONTEND_OUT}")
    print(f"  Hash: {sync_hash}")


if __name__ == "__main__":
    main()
