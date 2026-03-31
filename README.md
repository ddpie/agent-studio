# Agent Studio

基于 AWS Bedrock AgentCore 的 Agent 智能编排系统。通过自然语言创建、管理和运行 AI Agent。

## 架构

```
Web Console (React) → API Gateway → Meta-Agent (AgentCore Runtime)
                                         ↓
                                    AgentCore Runtime (子 Agent)
                                    AgentCore Gateway (MCP 工具)
                                    DynamoDB (元数据)
                                    S3 (Skill 文件 + Agent 代码)
```

## 项目结构

```
agent-studio/
├── meta-agent/          # 编排引擎 (Strands Agent on AgentCore)
│   ├── main.py          # 入口
│   ├── tools/           # Meta-Agent 的 9 个工具
│   └── templates/       # Agent 代码生成模板
├── mcp-lambdas/         # 预置 MCP 的 Lambda 函数
├── frontend/            # Web Console (React 19 + Vite)
└── infra/               # IaC (SAM/CDK)
```

## 技术栈

| 层 | 技术 |
|----|------|
| Agent 框架 | Strands Agents |
| Agent 运行时 | AWS Bedrock AgentCore Runtime |
| MCP 工具 | AgentCore Gateway (Remote MCP) |
| 前端 | React 19 + Vite 7 + shadcn/ui + Tailwind 4 |
| 认证 | Amazon Cognito |
| 数据 | DynamoDB + S3 |

## 开发

```bash
# Meta-Agent 本地开发
cd meta-agent
agentcore dev

# 前端开发
cd frontend
npm run dev
```
