"""Auto-generated MCP IAM policy registry — DO NOT EDIT.

Source of truth: mcp-runtime/mcp-registry.yaml
Regenerate:     python scripts/sync-mcp-iam-policies.py
"""
# SYNC_HASH: 62af13e4f1b9


MCP_IAM_POLICIES: dict[str, dict | None] = {
    "cloudtrail": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "cloudtrail:LookupEvents",
                    "cloudtrail:Describe*",
                    "cloudtrail:Get*",
                    "cloudtrail:List*",
                    "cloudtrail:StartQuery"
                ],
                "Resource": "*",
                "Sid": "Cloudtrail"
            }
        ]
    },
    "cloudwatch": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "cloudwatch:Describe*",
                    "cloudwatch:Get*",
                    "cloudwatch:List*",
                    "logs:Describe*",
                    "logs:Get*",
                    "logs:List*",
                    "logs:StartQuery",
                    "logs:StopQuery",
                    "logs:FilterLogEvents",
                    "application-signals:Get*",
                    "application-signals:List*"
                ],
                "Resource": "*",
                "Sid": "Cloudwatch"
            }
        ]
    },
    "cloudwatch-applicationsignals": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "application-signals:GetService",
                    "application-signals:ListServices",
                    "application-signals:GetServiceLevelObjective",
                    "application-signals:ListServiceLevelObjectives",
                    "application-signals:ListServiceOperations",
                    "application-signals:ListServiceDependencies",
                    "application-signals:ListServiceDependents",
                    "cloudwatch:DescribeAlarms",
                    "cloudwatch:GetMetricData",
                    "cloudwatch:GetMetricStatistics"
                ],
                "Resource": "*",
                "Sid": "CloudwatchApplicationsignals"
            }
        ]
    },
    "prometheus": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "aps:QueryMetrics",
                    "aps:GetLabels",
                    "aps:GetMetricMetadata",
                    "aps:GetSeries",
                    "aps:ListWorkspaces",
                    "aps:DescribeWorkspace"
                ],
                "Resource": "*",
                "Sid": "Prometheus"
            }
        ]
    },
    "acm": {
        "Statement": [
            {
                "Sid": "Acm",
                "Effect": "Allow",
                "Action": [
                    "acm:Describe*",
                    "acm:List*",
                    "acm:GetCertificate"
                ],
                "Resource": "*"
            }
        ]
    },
    "cognito": {
        "Statement": [
            {
                "Sid": "Cognito",
                "Effect": "Allow",
                "Action": [
                    "cognito-idp:Describe*",
                    "cognito-idp:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "config": {
        "Statement": [
            {
                "Sid": "Config",
                "Effect": "Allow",
                "Action": [
                    "config:Describe*",
                    "config:Get*",
                    "config:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "guardduty": {
        "Statement": [
            {
                "Sid": "GuardDuty",
                "Effect": "Allow",
                "Action": [
                    "guardduty:Get*",
                    "guardduty:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "iam": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "iam:Get*",
                    "iam:List*",
                    "iam:Simulate*"
                ],
                "Resource": "*",
                "Sid": "Iam"
            }
        ]
    },
    "inspector": {
        "Statement": [
            {
                "Sid": "Inspector",
                "Effect": "Allow",
                "Action": [
                    "inspector2:Get*",
                    "inspector2:List*",
                    "inspector2:BatchGet*"
                ],
                "Resource": "*"
            }
        ]
    },
    "kms": {
        "Statement": [
            {
                "Sid": "Kms",
                "Effect": "Allow",
                "Action": [
                    "kms:Describe*",
                    "kms:List*",
                    "kms:Get*"
                ],
                "Resource": "*"
            }
        ]
    },
    "security-hub": {
        "Statement": [
            {
                "Sid": "SecurityHub",
                "Effect": "Allow",
                "Action": [
                    "securityhub:Get*",
                    "securityhub:List*",
                    "securityhub:BatchGet*"
                ],
                "Resource": "*"
            }
        ]
    },
    "sts": {
        "Statement": [
            {
                "Sid": "Sts",
                "Effect": "Allow",
                "Action": [
                    "sts:GetCallerIdentity"
                ],
                "Resource": "*"
            }
        ]
    },
    "well-architected-security": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "wellarchitected:GetWorkload",
                    "wellarchitected:GetLensReview",
                    "wellarchitected:GetAnswer",
                    "wellarchitected:ListWorkloads",
                    "wellarchitected:ListLenses",
                    "wellarchitected:ListLensReviews",
                    "wellarchitected:ListAnswers"
                ],
                "Resource": "*",
                "Sid": "WellArchitectedSecurity"
            }
        ]
    },
    "billing-cost-management": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ce:GetCostAndUsage",
                    "ce:GetCostForecast",
                    "ce:DescribeCostCategoryDefinition",
                    "budgets:ViewBudget",
                    "budgets:DescribeBudget"
                ],
                "Resource": "*",
                "Sid": "BillingCostManagement"
            }
        ]
    },
    "cost-explorer": {
        "Statement": [
            {
                "Sid": "CostExplorer",
                "Effect": "Allow",
                "Action": [
                    "ce:Get*",
                    "ce:Describe*",
                    "ce:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "autoscaling": {
        "Statement": [
            {
                "Sid": "AutoScaling",
                "Effect": "Allow",
                "Action": [
                    "autoscaling:Describe*"
                ],
                "Resource": "*"
            }
        ]
    },
    "ec2": {
        "Statement": [
            {
                "Sid": "Ec2",
                "Effect": "Allow",
                "Action": [
                    "ec2:Describe*"
                ],
                "Resource": "*"
            }
        ]
    },
    "ecr": {
        "Statement": [
            {
                "Sid": "Ecr",
                "Effect": "Allow",
                "Action": [
                    "ecr:Describe*",
                    "ecr:List*",
                    "ecr:BatchGetImage"
                ],
                "Resource": "*"
            }
        ]
    },
    "ecs": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ecs:DescribeClusters",
                    "ecs:DescribeServices",
                    "ecs:DescribeTasks",
                    "ecs:DescribeTaskDefinition",
                    "ecs:DescribeContainerInstances",
                    "ecs:ListClusters",
                    "ecs:ListServices",
                    "ecs:ListTasks",
                    "ecs:ListTaskDefinitions"
                ],
                "Resource": "*",
                "Sid": "Ecs"
            }
        ]
    },
    "eks": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "eks:DescribeCluster",
                    "eks:DescribeNodegroup",
                    "eks:DescribeFargateProfile",
                    "eks:DescribeAddon",
                    "eks:ListClusters",
                    "eks:ListNodegroups",
                    "eks:ListFargateProfiles",
                    "eks:ListAddons"
                ],
                "Resource": "*",
                "Sid": "Eks"
            }
        ]
    },
    "lambda-tool": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "lambda:GetFunction",
                    "lambda:ListFunctions",
                    "lambda:GetPolicy",
                    "logs:DescribeLogGroups",
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents"
                ],
                "Resource": "*",
                "Sid": "LambdaTool"
            }
        ]
    },
    "stepfunctions-tool": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "states:DescribeStateMachine",
                    "states:ListStateMachines",
                    "states:DescribeExecution",
                    "states:ListExecutions"
                ],
                "Resource": "*",
                "Sid": "StepfunctionsTool"
            }
        ]
    },
    "efs": {
        "Statement": [
            {
                "Sid": "Efs",
                "Effect": "Allow",
                "Action": [
                    "elasticfilesystem:Describe*"
                ],
                "Resource": "*"
            }
        ]
    },
    "s3-readonly": {
        "Statement": [
            {
                "Sid": "S3ReadOnly",
                "Effect": "Allow",
                "Action": [
                    "s3:GetBucketLocation",
                    "s3:GetBucketTagging",
                    "s3:ListAllMyBuckets",
                    "s3:ListBucket"
                ],
                "Resource": "*"
            }
        ]
    },
    "aurora-dsql": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dsql:DbConnectAdmin",
                    "dsql:GetCluster",
                    "dsql:ListClusters"
                ],
                "Resource": "*",
                "Sid": "AuroraDsql"
            }
        ]
    },
    "documentdb": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "rds:DescribeDBClusters",
                    "rds:DescribeDBInstances"
                ],
                "Resource": "*",
                "Sid": "Documentdb"
            }
        ]
    },
    "dynamodb": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:DescribeTable",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:GetItem",
                    "dynamodb:BatchGetItem",
                    "dynamodb:ListTables"
                ],
                "Resource": "*",
                "Sid": "Dynamodb"
            }
        ]
    },
    "dynamodb-readonly": {
        "Statement": [
            {
                "Sid": "DynamoDBReadOnly",
                "Effect": "Allow",
                "Action": [
                    "dynamodb:Describe*",
                    "dynamodb:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "elasticache": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "elasticache:DescribeCacheClusters",
                    "elasticache:DescribeReplicationGroups",
                    "elasticache:ListTagsForResource"
                ],
                "Resource": "*",
                "Sid": "Elasticache"
            }
        ]
    },
    "memcached": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "elasticache:DescribeCacheClusters",
                    "elasticache:ListTagsForResource"
                ],
                "Resource": "*",
                "Sid": "Memcached"
            }
        ]
    },
    "mysql": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "rds:DescribeDBInstances",
                    "rds:DescribeDBClusters",
                    "rds-db:connect"
                ],
                "Resource": "*",
                "Sid": "Mysql"
            }
        ]
    },
    "neptune": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "neptune-db:ReadDataViaQuery",
                    "rds:DescribeDBClusters",
                    "rds:DescribeDBInstances"
                ],
                "Resource": "*",
                "Sid": "Neptune"
            }
        ]
    },
    "opensearch": {
        "Statement": [
            {
                "Sid": "OpenSearch",
                "Effect": "Allow",
                "Action": [
                    "es:Describe*",
                    "es:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "postgres": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "rds:DescribeDBInstances",
                    "rds:DescribeDBClusters",
                    "rds-db:connect"
                ],
                "Resource": "*",
                "Sid": "Postgres"
            }
        ]
    },
    "rds": {
        "Statement": [
            {
                "Sid": "Rds",
                "Effect": "Allow",
                "Action": [
                    "rds:Describe*",
                    "rds:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "redshift": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "redshift:DescribeClusters",
                    "redshift:GetClusterCredentials",
                    "redshift-data:ExecuteStatement",
                    "redshift-data:DescribeStatement",
                    "redshift-data:GetStatementResult"
                ],
                "Resource": "*",
                "Sid": "Redshift"
            }
        ]
    },
    "s3-tables": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3tables:GetTable",
                    "s3tables:GetTableBucket",
                    "s3tables:ListTables",
                    "s3tables:ListTableBuckets",
                    "s3tables:GetTableMetadataLocation"
                ],
                "Resource": "*",
                "Sid": "S3Tables"
            }
        ]
    },
    "timestream-for-influxdb": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "timestream:DescribeEndpoints",
                    "timestream:SelectValues",
                    "timestream:DescribeDatabase",
                    "timestream:DescribeTable",
                    "timestream:ListDatabases",
                    "timestream:ListTables"
                ],
                "Resource": "*",
                "Sid": "TimestreamForInfluxdb"
            }
        ]
    },
    "valkey": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "elasticache:DescribeReplicationGroups",
                    "elasticache:DescribeServerlessCaches",
                    "elasticache:ListTagsForResource"
                ],
                "Resource": "*",
                "Sid": "Valkey"
            }
        ]
    },
    "api-gateway": {
        "Statement": [
            {
                "Sid": "ApiGateway",
                "Effect": "Allow",
                "Action": [
                    "apigateway:GET"
                ],
                "Resource": "*"
            }
        ]
    },
    "appsync": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "appsync:GetGraphqlApi",
                    "appsync:GetSchemaCreationStatus",
                    "appsync:GetType",
                    "appsync:ListGraphqlApis",
                    "appsync:ListTypes",
                    "appsync:ListResolvers",
                    "appsync:ListDataSources"
                ],
                "Resource": "*",
                "Sid": "Appsync"
            }
        ]
    },
    "cloudfront": {
        "Statement": [
            {
                "Sid": "CloudFront",
                "Effect": "Allow",
                "Action": [
                    "cloudfront:Get*",
                    "cloudfront:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "elb": {
        "Statement": [
            {
                "Sid": "Elb",
                "Effect": "Allow",
                "Action": [
                    "elasticloadbalancing:Describe*"
                ],
                "Resource": "*"
            }
        ]
    },
    "network": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeSecurityGroups",
                    "ec2:DescribeRouteTables",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeInternetGateways",
                    "ec2:DescribeNatGateways",
                    "ec2:DescribeVpcPeeringConnections"
                ],
                "Resource": "*",
                "Sid": "Network"
            }
        ]
    },
    "route53": {
        "Statement": [
            {
                "Sid": "Route53",
                "Effect": "Allow",
                "Action": [
                    "route53:Get*",
                    "route53:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "amazon-mq": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "mq:DescribeBroker",
                    "mq:ListBrokers",
                    "mq:DescribeConfiguration",
                    "mq:ListConfigurations"
                ],
                "Resource": "*",
                "Sid": "AmazonMq"
            }
        ]
    },
    "eventbridge": {
        "Statement": [
            {
                "Sid": "EventBridge",
                "Effect": "Allow",
                "Action": [
                    "events:Describe*",
                    "events:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "msk": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "kafka:DescribeCluster",
                    "kafka:DescribeClusterV2",
                    "kafka:ListClusters",
                    "kafka:ListClustersV2",
                    "kafka:GetBootstrapBrokers",
                    "kafka:ListNodes",
                    "kafka:DescribeConfiguration"
                ],
                "Resource": "*",
                "Sid": "Msk"
            }
        ]
    },
    "sns": {
        "Statement": [
            {
                "Sid": "Sns",
                "Effect": "Allow",
                "Action": [
                    "sns:Get*",
                    "sns:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "sns-sqs": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "sns:ListTopics",
                    "sns:GetTopicAttributes",
                    "sns:ListSubscriptionsByTopic",
                    "sqs:ListQueues",
                    "sqs:GetQueueAttributes",
                    "sqs:GetQueueUrl"
                ],
                "Resource": "*",
                "Sid": "SnsSqs"
            }
        ]
    },
    "sqs": {
        "Statement": [
            {
                "Sid": "Sqs",
                "Effect": "Allow",
                "Action": [
                    "sqs:Get*",
                    "sqs:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "kendra-index": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "kendra:Query",
                    "kendra:Retrieve",
                    "kendra:DescribeIndex",
                    "kendra:ListIndices"
                ],
                "Resource": "*",
                "Sid": "KendraIndex"
            }
        ]
    },
    "qbusiness-anonymous": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "qbusiness:ChatSync",
                    "qbusiness:ListMessages",
                    "qbusiness:ListApplications",
                    "qbusiness:GetApplication"
                ],
                "Resource": "*",
                "Sid": "QbusinessAnonymous"
            }
        ]
    },
    "qindex": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "qbusiness:ChatSync",
                    "qbusiness:ListMessages",
                    "qbusiness:ListApplications",
                    "qbusiness:GetApplication"
                ],
                "Resource": "*",
                "Sid": "Qindex"
            }
        ]
    },
    "athena": {
        "Statement": [
            {
                "Sid": "Athena",
                "Effect": "Allow",
                "Action": [
                    "athena:Get*",
                    "athena:List*",
                    "athena:BatchGet*"
                ],
                "Resource": "*"
            }
        ]
    },
    "glue": {
        "Statement": [
            {
                "Sid": "Glue",
                "Effect": "Allow",
                "Action": [
                    "glue:Get*",
                    "glue:List*",
                    "glue:BatchGet*"
                ],
                "Resource": "*"
            }
        ]
    },
    "kinesis": {
        "Statement": [
            {
                "Sid": "Kinesis",
                "Effect": "Allow",
                "Action": [
                    "kinesis:Describe*",
                    "kinesis:List*",
                    "kinesis:Get*"
                ],
                "Resource": "*"
            }
        ]
    },
    "dataprocessing": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetJob",
                    "glue:GetJobs",
                    "glue:GetJobRun",
                    "glue:GetJobRuns",
                    "glue:GetCrawler",
                    "glue:GetCrawlers"
                ],
                "Resource": "*",
                "Sid": "Dataprocessing"
            }
        ]
    },
    "bedrock-agentcore": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:ListAgentRuntimes",
                    "bedrock-agentcore:GetAgentRuntime",
                    "bedrock-agentcore:ListGateways",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:ListGatewayTargets",
                    "bedrock-agentcore:GetGatewayTarget"
                ],
                "Resource": "*",
                "Sid": "BedrockAgentcore"
            }
        ]
    },
    "bedrock-data-automation": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject"
                ],
                "Resource": "*",
                "Sid": "BedrockDataAutomation"
            }
        ]
    },
    "bedrock-kb-retrieval": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate",
                    "bedrock:ListKnowledgeBases",
                    "bedrock:GetKnowledgeBase"
                ],
                "Resource": "*",
                "Sid": "BedrockKbRetrieval"
            }
        ]
    },
    "bedrock-readonly": {
        "Statement": [
            {
                "Sid": "BedrockReadOnly",
                "Effect": "Allow",
                "Action": [
                    "bedrock:Get*",
                    "bedrock:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "sagemaker": {
        "Statement": [
            {
                "Sid": "SageMaker",
                "Effect": "Allow",
                "Action": [
                    "sagemaker:Describe*",
                    "sagemaker:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "sagemaker-ai": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "sagemaker:DescribeEndpoint",
                    "sagemaker:DescribeEndpointConfig",
                    "sagemaker:DescribeModel",
                    "sagemaker:DescribeTrainingJob",
                    "sagemaker:ListEndpoints",
                    "sagemaker:ListModels",
                    "sagemaker:ListTrainingJobs"
                ],
                "Resource": "*",
                "Sid": "SagemakerAi"
            }
        ]
    },
    "healthlake": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "healthlake:DescribeFHIRDatastore",
                    "healthlake:ListFHIRDatastores",
                    "healthlake:ReadResource",
                    "healthlake:SearchWithGet",
                    "healthlake:SearchWithPost"
                ],
                "Resource": "*",
                "Sid": "Healthlake"
            }
        ]
    },
    "healthomics": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "omics:GetWorkflow",
                    "omics:GetRun",
                    "omics:GetRunTask",
                    "omics:ListWorkflows",
                    "omics:ListRuns",
                    "omics:ListRunTasks",
                    "omics:GetSequenceStore",
                    "omics:ListSequenceStores"
                ],
                "Resource": "*",
                "Sid": "Healthomics"
            }
        ]
    },
    "iot-sitewise": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "iotsitewise:DescribeAsset",
                    "iotsitewise:DescribeAssetModel",
                    "iotsitewise:DescribeAssetProperty",
                    "iotsitewise:GetAssetPropertyValue",
                    "iotsitewise:GetAssetPropertyValueHistory",
                    "iotsitewise:ListAssets",
                    "iotsitewise:ListAssetModels",
                    "iotsitewise:BatchGetAssetPropertyValue"
                ],
                "Resource": "*",
                "Sid": "IotSitewise"
            }
        ]
    },
    "location": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "geo:SearchPlaceIndexForText",
                    "geo:SearchPlaceIndexForPosition",
                    "geo:SearchPlaceIndexForSuggestions",
                    "geo:GetPlace",
                    "geo:CalculateRoute",
                    "geo:ListPlaceIndexes",
                    "geo:ListRouteCalculators"
                ],
                "Resource": "*",
                "Sid": "Location"
            }
        ]
    },
    "support": {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "support:DescribeTrustedAdvisorChecks",
                    "support:DescribeTrustedAdvisorCheckResult",
                    "support:DescribeCases",
                    "support:DescribeServices",
                    "support:DescribeSeverityLevels"
                ],
                "Resource": "*",
                "Sid": "Support"
            }
        ]
    },
    "backup": {
        "Statement": [
            {
                "Sid": "Backup",
                "Effect": "Allow",
                "Action": [
                    "backup:Describe*",
                    "backup:Get*",
                    "backup:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "cloudformation": {
        "Statement": [
            {
                "Sid": "CloudFormation",
                "Effect": "Allow",
                "Action": [
                    "cloudformation:Describe*",
                    "cloudformation:List*",
                    "cloudformation:GetTemplateSummary"
                ],
                "Resource": "*"
            }
        ]
    },
    "compute-optimizer": {
        "Statement": [
            {
                "Sid": "ComputeOptimizer",
                "Effect": "Allow",
                "Action": [
                    "compute-optimizer:Get*"
                ],
                "Resource": "*"
            }
        ]
    },
    "health": {
        "Statement": [
            {
                "Sid": "Health",
                "Effect": "Allow",
                "Action": [
                    "health:Describe*"
                ],
                "Resource": "*"
            }
        ]
    },
    "service-quotas": {
        "Statement": [
            {
                "Sid": "ServiceQuotas",
                "Effect": "Allow",
                "Action": [
                    "servicequotas:Get*",
                    "servicequotas:List*"
                ],
                "Resource": "*"
            }
        ]
    },
    "ssm": {
        "Statement": [
            {
                "Sid": "Ssm",
                "Effect": "Allow",
                "Action": [
                    "ssm:DescribeParameters",
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:List*"
                ],
                "Resource": "*"
            }
        ]
    },
}

# Targets that exist but require no IAM permissions.
_NO_IAM_TARGETS = {'aws-api', 'aws-knowledge', 'aws-pricing', 'code-doc-gen', 'diagram', 'openapi', 'syntheticdata'}
