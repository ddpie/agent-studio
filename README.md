# Agent Studio

基于 AWS Bedrock AgentCore 的 Agent 智能编排系统。通过自然语言创建、管理和运行 AI Agent。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React 19 + Vite + Tailwind 4)                    │
│  Cognito Auth → SigV4 签名 → AgentCore / S3 / DynamoDB     │
└──────────────┬──────────────────────────────────────────────┘
               │ SigV4 Streaming
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Meta-Agent (Strands SDK on AgentCore Runtime)              │
│  25 个工具 — 管理 Sub-Agent 全生命周期                        │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │ 创建/更新 │ 诊断/日志 │ Skill 管理│ 工具库    │ MCP/Secrets│ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
└──────────────┬──────────────────────────────────────────────┘
               │ boto3 (bedrock-agentcore-control / bedrock-agentcore)
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Sub-Agents (AgentCore Runtime)                             │
│  每个 Agent: main.py + tools.py + prompt.txt + config.json  │
│  ┌──────────┬──────────┬──────────┐                         │
│  │ AWS APIs │ MCP GW   │ Skills   │                         │
│  └──────────┴──────────┴──────────┘                         │
└─────────────────────────────────────────────────────────────┘

存储: S3 (部署包/配置/Skill) + DynamoDB (归属/权限)
认证: Cognito User Pool + Identity Pool → SigV4
```

## 项目结构

```
agent-studio/
├── frontend/                    # Web Console
│   ├── src/
│   │   ├── components/
│   │   │   ├── chat/            # ChatPanel (流式对话、tool 可视化、多模态)
│   │   │   ├── agents/          # AgentList, AgentEditForm, EditAssistant
│   │   │   ├── skills/          # SkillAssistant
│   │   │   ├── tools/           # ToolAssistant
│   │   │   ├── pages/           # SkillDetail, SkillsPage, McpPage, SettingsPage, ToolLibrary
│   │   │   ├── layout/          # AppShell, IconNav, AgentsLayout
│   │   │   └── ui/              # ConfirmDialog, ImageLightbox
│   │   ├── stores/              # Zustand (8 个 store)
│   │   │   ├── chat-store.ts    # 会话管理、流式消息、多 session
│   │   │   ├── agent-list-store.ts
│   │   │   ├── agent-edit-store.ts
│   │   │   ├── edit-assistant-store.ts
│   │   │   ├── skill-assistant-store.ts
│   │   │   ├── tool-library-store.ts
│   │   │   ├── tool-assistant-store.ts
│   │   │   └── ui-settings-store.ts
│   │   ├── lib/                 # 工具函数
│   │   │   ├── agentcore-client.ts  # SigV4 签名 + SSE 流解析
│   │   │   ├── tool-extractor.ts    # 从 deployment.zip 提取 @tool 定义
│   │   │   ├── skill-storage.ts     # Skill CRUD (S3 直连)
│   │   │   ├── s3-storage.ts        # S3 通用读写
│   │   │   ├── s3-utils.ts          # 图片/文件上传
│   │   │   ├── agent-metadata.ts    # Agent 元数据读取
│   │   │   ├── models.ts            # 18 个模型定义
│   │   │   └── pyodide-checker.ts   # 浏览器端 Python 语法检查
│   │   ├── router.tsx
│   │   └── config.ts
│   └── package.json
├── meta-agent/                  # 编排引擎
│   ├── main.py                  # Meta-Agent 入口 (25 个工具)
│   ├── tools/                   # Agent 生命周期工具
│   │   ├── create_agent.py      # 创建 + 部署
│   │   ├── update_agent.py      # 更新配置/重新部署
│   │   ├── delete_agent.py      # 删除/归档/恢复/清除
│   │   ├── validate_agent.py    # 部署前校验 (语法/权限/安全)
│   │   ├── invoke_agent.py      # 测试调用
│   │   ├── check_agent_logs.py  # CloudWatch 日志
│   │   ├── analyze_trace.py     # Agent Trace 分析
│   │   ├── create_skill.py / update_skill.py / delete_skill.py / import_skill.py
│   │   ├── manage_secrets.py    # Secrets Manager 集成
│   │   ├── create_schedule.py   # 定时调用
│   │   ├── preview_code.py      # 预览组装后的代码
│   │   └── list_mcp_servers.py  # MCP Gateway 服务发现
│   ├── tools_library/           # 预构建工具库 (6 个)
│   │   ├── registry.py          # 工具注册/目录生成
│   │   ├── web_search.py, fetch_webpage.py, s3_read.py
│   │   ├── sql_readonly.py, translate.py, chart_generator.py
│   │   └── __init__.py
│   ├── templates/               # 代码生成 + 提示词模板
│   │   ├── agent_template_v2.py # 多文件部署模板 (main/tools/prompt/config/stream_utils/builtin_tools)
│   │   ├── prompt_templates.py  # 5 种 Agent 模板 (通用/专家/客服/数据分析/创意写手)
│   │   └── agent_template.py    # Legacy 单文件模板
│   ├── config.py                # Region, S3, DynamoDB, IAM Roles, 权限档位
│   ├── deploy.py                # 打包/上传/创建 Runtime
│   ├── tests/                   # 单元测试 (pytest)
│   └── pyproject.toml
├── scripts/
│   ├── run-tests.sh             # 一键跑全部测试 + 覆盖率
│   ├── deploy-agentcore.sh      # 部署 Meta-Agent 到 AgentCore
│   └── build-frontend.sh        # 构建前端
├── .env.example
└── .gitignore
```

## 核心功能

**Agent 管理**
- 自然语言创建 Agent — Meta-Agent 引导设计、确认、部署，输出可编辑的 agent-proposal 卡片
- 表单编辑 — 直接编辑系统提示词、工具代码、欢迎词，自动判断是否需要重新部署
- 部署前校验 — 语法检查、tool_names 一致性、禁用库检测、写操作权限检查、LLM prompt 质量评分
- 归档/恢复/清除 — 软删除 + 恢复 + 永久清除

**对话与模型**
- 18 个模型 — Claude 4.6/4.5/4/3.x（Opus/Sonnet/Haiku），运行时动态切换
- 多模态 — 图片粘贴/URL 输入，per-agent 图片支持开关
- 流式响应 — SSE + tool-use 可视化（调用过程、输入输出 base64 编码）
- 多 session — 自动保存、跨 session 切换、按 Agent 记忆上次 session

**Skill 系统**
- AgentSkills.io 格式 — YAML frontmatter + Markdown body
- 多文件 Skill — SKILL.md + 附属脚本/数据文件，Monaco Editor 编辑
- 导入/导出 — 从 URL 或原始 Markdown 导入，自动包装 frontmatter
- 运行时发现 — Sub-Agent 启动时自动加载 skills/index.json，按需 load_skill()

**工具与集成**
- 预构建工具库 — web_search, fetch_webpage, s3_read, sql_readonly, translate, chart_generator
- MCP Gateway — AgentCore Gateway 远程 MCP (Streamable HTTP, SigV4 认证)
- Secrets Manager — Agent 级别 API key 管理
- 定时调用 — cron 式 Agent 调度

**安全与权限**
- Cognito 认证 — User Pool + Identity Pool → SigV4 签名
- 权限档位 — basic / readonly / data-access 三档 IAM Role
- DynamoDB 归属 — 按 Cognito userId 过滤 Agent 可见性

## 技术栈

| 层 | 技术 |
|----|------|
| Agent 框架 | Strands Agents SDK |
| Agent 运行时 | AWS Bedrock AgentCore Runtime |
| MCP 工具 | AgentCore Gateway (Remote MCP, Streamable HTTP) |
| 前端 | React 19 + Vite 8 + Tailwind 4 + Zustand 5 |
| 代码编辑 | Monaco Editor (语法高亮、Diff 视图) |
| 语法检查 | Pyodide (浏览器端 CPython WASM) |
| 认证 | Amazon Cognito (User Pool + Identity Pool) |
| 数据 | DynamoDB (归属/权限) + S3 (部署包/配置/Skill) |
| 密钥 | AWS Secrets Manager |
| 模型 | Claude 4.6/4.5/4/3.x via Amazon Bedrock |

## 快速开始

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 AWS 账号信息

# 2. 部署 Meta-Agent
bash scripts/deploy-agentcore.sh

# 3. 启动前端
cd frontend
npm install
npm run dev
```

## 测试

```bash
# 一键跑全部测试 + 覆盖率报告
bash scripts/run-tests.sh

# 单独跑
cd meta-agent && PYTHONPATH=. python3 -m pytest tests/ -v   # Backend (65 tests)
cd frontend && npm test                                      # Frontend (45 tests)
```

覆盖模块：validate_agent (语法/权限/安全校验)、prompt_templates (模板注册)、tool-extractor (@tool 解析器)、skill-storage (YAML frontmatter 解析)

## 环境变量

| 变量 | 说明 |
|------|------|
| `AGENT_STUDIO_REGION` | AWS Region |
| `AGENT_STUDIO_ACCOUNT_ID` | AWS Account ID |
| `AGENT_STUDIO_META_AGENT_ID` | Meta-Agent Runtime ID |
| `AGENT_STUDIO_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `AGENT_STUDIO_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID` | Cognito Identity Pool ID |
| `AGENT_STUDIO_S3_BUCKET` | S3 Bucket（部署包 + 配置 + Skill） |

---

# Agent Studio (English)

An AI agent orchestration platform built on AWS Bedrock AgentCore. Create, manage, and run AI agents through natural language.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (React 19 + Vite + Tailwind 4)                    │
│  Cognito Auth → SigV4 signing → AgentCore / S3 / DynamoDB   │
└──────────────┬──────────────────────────────────────────────┘
               │ SigV4 Streaming
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Meta-Agent (Strands SDK on AgentCore Runtime)              │
│  25 tools — full sub-agent lifecycle management              │
│  ┌──────────┬──────────┬──────────┬──────────┬────────────┐ │
│  │ CRUD     │ Diagnose │ Skills   │ Tool Lib │ MCP/Secrets│ │
│  └──────────┴──────────┴──────────┴──────────┴────────────┘ │
└──────────────┬──────────────────────────────────────────────┘
               │ boto3 (bedrock-agentcore-control / bedrock-agentcore)
               ▼
┌─────────────────────────────────────────────────────────────┐
│  Sub-Agents (AgentCore Runtime)                             │
│  Per agent: main.py + tools.py + prompt.txt + config.json   │
│  ┌──────────┬──────────┬──────────┐                         │
│  │ AWS APIs │ MCP GW   │ Skills   │                         │
│  └──────────┴──────────┴──────────┘                         │
└─────────────────────────────────────────────────────────────┘

Storage: S3 (packages/config/skills) + DynamoDB (ownership/permissions)
Auth:    Cognito User Pool + Identity Pool → SigV4
```

## Features

**Agent Management**
- Natural language creation — Meta-Agent guides design, review, and deployment via editable agent-proposal cards
- Form-based editing — edit system prompts, tool code, welcome messages; auto-detects if redeploy is needed
- Pre-deploy validation — syntax check, tool_names consistency, blocked library detection, write-op permission check, LLM prompt quality scoring
- Archive / restore / purge — soft delete with recovery

**Chat & Models**
- 18 models — Claude 4.6/4.5/4/3.x (Opus/Sonnet/Haiku), switchable at runtime
- Multimodal — paste images or URLs; per-agent image support toggle
- Streaming — SSE with tool-use visualization (call progress, base64-encoded I/O)
- Multi-session — auto-save, cross-session switching, per-agent session memory

**Skill System**
- AgentSkills.io format — YAML frontmatter + Markdown body
- Multi-file skills — SKILL.md + attached scripts/data, Monaco Editor
- Import/export — from URL or raw Markdown, auto-wraps frontmatter
- Runtime discovery — sub-agents auto-load skills/index.json, on-demand load_skill()

**Tools & Integrations**
- Built-in tool library — web_search, fetch_webpage, s3_read, sql_readonly, translate, chart_generator
- MCP Gateway — AgentCore Gateway remote MCP (Streamable HTTP, SigV4 auth)
- Secrets Manager — per-agent API key management
- Scheduled invocations — cron-style agent scheduling

**Security & Permissions**
- Cognito auth — User Pool + Identity Pool → SigV4 signing
- Permission tiers — basic / readonly / data-access IAM roles
- DynamoDB ownership — agent visibility filtered by Cognito userId

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | Strands Agents SDK |
| Agent Runtime | AWS Bedrock AgentCore Runtime |
| MCP Tools | AgentCore Gateway (Remote MCP, Streamable HTTP) |
| Frontend | React 19 + Vite 8 + Tailwind 4 + Zustand 5 |
| Code Editor | Monaco Editor (syntax highlighting, diff view) |
| Syntax Check | Pyodide (in-browser CPython WASM) |
| Auth | Amazon Cognito (User Pool + Identity Pool) |
| Data | DynamoDB (ownership/permissions) + S3 (packages/config/skills) |
| Secrets | AWS Secrets Manager |
| Models | Claude 4.6/4.5/4/3.x via Amazon Bedrock |

## Quick Start

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your AWS account details

# 2. Deploy Meta-Agent
bash scripts/deploy-agentcore.sh

# 3. Start frontend
cd frontend
npm install
npm run dev
```

## Testing

```bash
# Run all tests with coverage
bash scripts/run-tests.sh

# Run individually
cd meta-agent && PYTHONPATH=. python3 -m pytest tests/ -v   # Backend (65 tests)
cd frontend && npm test                                      # Frontend (45 tests)
```

Covered modules: validate_agent (syntax/permission/security checks), prompt_templates (template registry), tool-extractor (@tool parser), skill-storage (YAML frontmatter parsing)

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AGENT_STUDIO_REGION` | AWS Region |
| `AGENT_STUDIO_ACCOUNT_ID` | AWS Account ID |
| `AGENT_STUDIO_META_AGENT_ID` | Meta-Agent Runtime ID |
| `AGENT_STUDIO_COGNITO_USER_POOL_ID` | Cognito User Pool ID |
| `AGENT_STUDIO_COGNITO_CLIENT_ID` | Cognito App Client ID |
| `AGENT_STUDIO_COGNITO_IDENTITY_POOL_ID` | Cognito Identity Pool ID |
| `AGENT_STUDIO_S3_BUCKET` | S3 Bucket for packages, config, and skills |
