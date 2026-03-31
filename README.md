# Agent Studio

基于 AWS Bedrock AgentCore 的 Agent 智能编排系统。通过自然语言创建、管理和运行 AI Agent。

## 架构

```
Frontend (React)  ──SigV4──▶  Meta-Agent (AgentCore Runtime)
                                    │  创建/编辑/删除/诊断
                                    ▼
                              Sub-Agents (AgentCore Runtime)
                                    │
                              ┌─────┼──────┐
                              ▼     ▼      ▼
                            AWS   MCP    CloudWatch
                            APIs  Gateway  Logs

存储: S3 (代码/配置/草稿) + DynamoDB (归属/权限)
认证: Cognito User Pool + Identity Pool → SigV4 签名
```

## 项目结构

```
agent-studio/
├── frontend/            # Web Console (React 19 + Vite + Tailwind 4)
│   ├── src/components/  # Chat, AgentList, AgentEdit, IconNav...
│   ├── src/stores/      # Zustand stores (chat, agent-list, agent-edit, nav)
│   ├── src/lib/         # AgentCore client (SigV4), agent metadata
│   └── src/config.ts    # 从环境变量读取配置
├── meta-agent/          # 编排引擎 (Strands Agent on AgentCore Runtime)
│   ├── main.py          # Meta-Agent 入口 (13 个工具)
│   ├── tools/           # create/update/delete/invoke/logs/skills/mcp...
│   ├── templates/       # Agent 代码生成模板 + 提示词模板库
│   ├── config.py        # 共享配置 (Region, S3, DynamoDB, IAM Roles)
│   └── deploy.py        # 部署工具 (打包/上传/创建 Runtime)
├── .env.example         # 环境变量模板
└── .gitignore
```

## 核心功能

- **自然语言创建 Agent** — 通过对话描述需求，Meta-Agent 引导设计、确认、部署
- **Agent 配置编辑** — 直接表单编辑系统提示词、工具、欢迎词等，自动判断是否需要重新部署
- **多模型支持** — 22 个模型（Claude 4.6/4.5/4/3.x、Llama 4/3、DeepSeek R1、Mistral），运行时动态切换
- **多模态** — 图片粘贴输入，Agent 可选择是否支持图片处理
- **会话管理** — 多 session 历史、自动保存、跨 session 切换
- **归属控制** — DynamoDB 存储 Agent 归属，按 Cognito userId 过滤
- **权限档位** — basic / readonly / data-access 三档 IAM Role
- **诊断工具** — CloudWatch 日志查看、Agent Trace 分析

## 技术栈

| 层 | 技术 |
|----|------|
| Agent 框架 | Strands Agents SDK |
| Agent 运行时 | AWS Bedrock AgentCore Runtime |
| MCP 工具 | AgentCore Gateway (Remote MCP, Streamable HTTP) |
| 前端 | React 19 + Vite 7 + Tailwind 4 + Zustand |
| 认证 | Amazon Cognito (User Pool + Identity Pool) |
| 数据 | DynamoDB (归属/权限) + S3 (代码/配置) |
| 模型 | Claude, Llama, DeepSeek, Mistral via Amazon Bedrock |

## 快速开始

```bash
# 1. 复制环境配置
cp .env.example .env
# 编辑 .env 填入你的 AWS 账号信息

# 2. 部署 Meta-Agent
cd meta-agent
pip install -e .
agentcore deploy

# 3. 启动前端
cd frontend
npm install
npm run dev
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `AGENT_STUDIO_REGION` | AWS Region |
| `AGENT_STUDIO_ACCOUNT_ID` | AWS Account ID |
| `AGENT_STUDIO_META_AGENT_ID` | Meta-Agent Runtime ID |
| `AGENT_STUDIO_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `AGENT_STUDIO_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID` | Cognito Identity Pool ID |
| `AGENT_STUDIO_S3_BUCKET` | S3 Bucket（部署包 + 配置） |

---

# Agent Studio (English)

An AI agent orchestration platform built on AWS Bedrock AgentCore. Create, manage, and run AI agents through natural language.

## Architecture

```
Frontend (React)  ──SigV4──▶  Meta-Agent (AgentCore Runtime)
                                    │  create / edit / delete / diagnose
                                    ▼
                              Sub-Agents (AgentCore Runtime)
                                    │
                              ┌─────┼──────┐
                              ▼     ▼      ▼
                            AWS   MCP    CloudWatch
                            APIs  Gateway  Logs

Storage: S3 (code/config/drafts) + DynamoDB (ownership/permissions)
Auth:    Cognito User Pool + Identity Pool → SigV4 signing
```

## Project Structure

```
agent-studio/
├── frontend/            # Web Console (React 19 + Vite + Tailwind 4)
│   ├── src/components/  # Chat, AgentList, AgentEdit, IconNav...
│   ├── src/stores/      # Zustand stores (chat, agent-list, agent-edit, nav)
│   ├── src/lib/         # AgentCore client (SigV4), agent metadata
│   └── src/config.ts    # Config from environment variables
├── meta-agent/          # Orchestration engine (Strands Agent on AgentCore)
│   ├── main.py          # Meta-Agent entrypoint (13 tools)
│   ├── tools/           # create/update/delete/invoke/logs/skills/mcp...
│   ├── templates/       # Agent code generation + prompt templates
│   ├── config.py        # Shared config (Region, S3, DynamoDB, IAM Roles)
│   └── deploy.py        # Deployment utils (package/upload/create runtime)
├── .env.example         # Environment variable template
└── .gitignore
```

## Features

| Feature | Description |
|---------|-------------|
| **Natural language agent creation** | Describe what you need; Meta-Agent guides design, review, and deployment |
| **Agent config editor** | Edit system prompts, tools, welcome messages via form; auto-detects if redeploy is needed |
| **22 models** | Claude 4.6/4.5/4/3.x, Llama 4/3, DeepSeek R1, Mistral — switch at runtime |
| **Multimodal** | Paste images in chat; per-agent toggle for image support |
| **Session management** | Multi-session history, auto-save, cross-session switching |
| **Ownership control** | DynamoDB-based agent ownership, filtered by Cognito userId |
| **Permission tiers** | basic / readonly / data-access IAM roles for sub-agents |
| **Diagnostics** | CloudWatch log viewer, agent trace analysis |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | Strands Agents SDK |
| Agent Runtime | AWS Bedrock AgentCore Runtime |
| MCP Tools | AgentCore Gateway (Remote MCP, Streamable HTTP) |
| Frontend | React 19 + Vite 7 + Tailwind 4 + Zustand |
| Auth | Amazon Cognito (User Pool + Identity Pool) |
| Data | DynamoDB (ownership/permissions) + S3 (code/config) |
| Models | Claude, Llama, DeepSeek, Mistral via Amazon Bedrock |

## Quick Start

```bash
# 1. Copy and fill in environment config
cp .env.example .env
# Edit .env with your AWS account details

# 2. Deploy Meta-Agent
cd meta-agent
pip install -e .
agentcore deploy

# 3. Start frontend
cd frontend
npm install
npm run dev
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENT_STUDIO_REGION` | AWS Region |
| `AGENT_STUDIO_ACCOUNT_ID` | AWS Account ID |
| `AGENT_STUDIO_META_AGENT_ID` | Meta-Agent Runtime ID |
| `AGENT_STUDIO_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `AGENT_STUDIO_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID` | Cognito Identity Pool ID |
| `AGENT_STUDIO_S3_BUCKET` | S3 Bucket for deployment packages and config |
