# Agent Studio

基于 AWS Bedrock AgentCore 的 Agent 编排平台。用自然语言向 Meta-Agent 描述需求，它为你创建、部署、维护可直接运行的 Agent。Meta-Agent 由 Kiro CLI 驱动，Agent 基于 Strands。

## 能做什么

- **对话创建 Agent** — 告诉 Meta-Agent 你的需求，它写 prompt、挑工具、生成代码、完成部署
- **可视化调试** — 对话流式返回，每次 tool 调用的输入输出都显示在消息流里
- **Agent 互调** — `link_agent` 把一个 Agent 挂为另一个的工具，通过 A2A 协议调用，密钥自动下发
- **知识库 (RAG)** — 对话式创建 KB，上传文档自动向量化（S3 Vectors + Cohere Multilingual v3），Agent 通过 `kb_retrieve` 按需检索
- **Skill 热插拔** — AgentSkills.io 格式，运行时按需加载，跨 Agent 复用
- **多模态输入** — 图片 / PDF / Excel / CSV / TSV 自动解析，无需额外配置
- **定时触发** — 可视化 cron 构建器，每次执行留痕为卡片，可直接查看运行详情
- **MCP 工具集** — AWS 官方 MCP 目录全量接入，按需启用；per-target IAM 角色，最小权限
- **Workspace IAM 隔离** — 三层权限模型（Permission Boundary 天花板 → per-workspace 角色 → target 级授权）；Admin Console 一键授权，敏感 target 二次确认；`SimulatePrincipalPolicy` 实时检查，进度条展示每个 target 的已授权/缺失 action
- **跨会话记忆** — 基于 AgentCore Memory，Agent 自动记住用户偏好与事实，跨 session、跨设备持续生效；用户可在记忆抽屉中查看和删除
- **平台级可观测性** — 调用追踪、延迟分位数、错误率、Token 成本按 Agent 和 workspace 自动聚合，一屏总览
- **Marketplace** — 跨 workspace 发布和克隆 Agent / Skill / Tool，元数据公开，源码克隆后才可见

## Meta-Agent 能力总览

Meta-Agent 内置 47 个工具，覆盖 Agent 从创建到运维的完整生命周期：

| 能力域 | 具体能力 |
|--------|---------|
| **Agent 全生命周期** | 创建、更新、验证（`validate_agent`）、部署、归档、恢复、永久删除；支持 zip 和 harness 两种运行时 |
| **Skill 系统** | 创建、编辑脚本文件、从 URL/Markdown 导入、挂载到 Agent、跨 Agent 同步更新、删除 |
| **多 Agent 编排** | A2A 链接/取消链接、调用其他 Agent、创建定时调度 |
| **知识库 (RAG)** | 创建 KB、上传文档、检查摄取状态、挂载/卸载到 Agent、删除 |
| **MCP 工具接入** | 浏览 50+ AWS 官方 MCP Server、查看 target 工具列表、workspace 权限校验后一键接入 |
| **运维 & 调试** | 实时日志查看、调用 trace 分析、打包代码预览、Agent Secrets 管理、workspace 权限检查 |
| **Prompt 工程** | 自动生成符合规范的 system prompt（约束分层、反模式预防、工具-Prompt 联动检查） |
| **内置工具库** | 浏览预构建工具模板（web_search、s3_read、chart 等），优先复用再自定义 |

所有操作通过自然语言对话触发，无需记忆工具名或参数。

## 两种 Agent 运行时

| 运行时 | 适合 | 不支持 |
|---|---|---|
| **zip**(默认) | 需要自定义 Python 工具、MCP、Skills、A2A 关联、浏览器/代码解释器 | — |
| **harness**(实验性) | 只需要 prompt + 模型 + 长期记忆,看重**创建可靠性**和**最短冷启动** | MCP、自定义工具、Skills、linked agents |

两者共享聊天、Memory、可观测、成本页面等所有平台能力;区别只在 Agent 执行层。

## 架构

```mermaid
graph LR
    User((用户)) --> Web[Web Console]
    Cron[定时器]
    Web -->|对话| Meta[Meta-Agent<br/>创建 / 管理]

    subgraph Agents
        direction TB
        A1[Agent A] -->|A2A| A2[Agent B]
        A2 ~~~ A3[Agent C]
    end

    Web -->|对话| Agents
    Meta -.部署.-> Agents
    Cron -.触发.-> Agents
    Agents --> LLM[Bedrock LLMs]
    Agents --> Ext[Skills · Tools · MCP]
    Agents <-->|记忆| Mem[AgentCore Memory]
    Agents -.observability.-> Obs[Traces · Evaluations<br/>Costs]
```

完整系统图（CloudFront / Lambda / EventBridge / Evaluator 等）与关键设计说明见 [docs/architecture.md](docs/architecture.md)。

## 安全与权限

Agent Studio 对每个 workspace 实施三层权限控制，在赋能 Agent 访问 AWS 资源的同时防止越权：

```
Permission Boundary（天花板）
  └─ Workspace Role（身份策略）
       └─ Target Grants（按需授权）
```

- **Permission Boundary** — `AgentStudioWorkspaceCeiling` 管理策略封顶 workspace 角色的最大权限。写操作、IAM 变更、角色提权被显式 Deny，无论身份策略怎么配都不会超出天花板
- **Per-workspace 角色** — 按需创建 `AgentStudio-ws-{id}` IAM 角色并绑定 Permission Boundary。未创建角色的 workspace 只能使用平台内置工具（搜索、图表、代码解释器）
- **Target 级授权** — 管理员在 Admin Console 按 MCP target 逐项授权（如 CloudWatch、DynamoDB、IAM）。IAM 策略从 `mcp-registry.yaml` 单一数据源自动生成，消除手工维护的漂移风险
- **敏感标识与二次确认** — 每个 target 标注 sensitivity 等级（low / medium / high）。medium 以上的授权操作弹出确认弹窗，展示风险原因和具体 action 列表
- **实时权限检查** — `SimulatePrincipalPolicy` 实时模拟，每个 target 展示进度条和已授权/缺失 action 明细。Agent 创建时 `validate_agent` 自动校验 MCP 权限

## 部署

### 前置条件

- 已配置凭据的 AWS 账号：`aws sts get-caller-identity` 能正常返回
- 使用的 IAM 身份能够 `cdk bootstrap`，并能创建 IAM 角色、策略与 Permission Boundary
- 本机装有 **Node.js 18+**、**Python 3.10+**、**Docker**（CDK Lambda bundling 需要 Docker daemon 在跑）；以及 `npm`、`jq`、`yq`——脚本会做版本预检

### 一键部署

首次部署两步走。先准备 `.env`：

```bash
cp .env.example .env
```

打开 `.env` 填入三项必填：

- `AGENT_STUDIO_REGION` — `us-east-1` 或 `us-west-2`
- `AGENT_STUDIO_ACCOUNT_ID` — 12 位 AWS 账号 ID
- `ORIGIN_VERIFY_SECRET` — 用 `openssl rand -hex 32` 生成

其它字段（`META_AGENT_ID`、`COGNITO_*`、`CLOUDFRONT_*`）部署脚本会自动回填。

然后一键部署，约 15–20 分钟：

```bash
bash scripts/deploy-all.sh --region us-east-1   # 或 --region us-west-2
```

脚本依次完成：CDK 基础设施（Cognito / Lambda / CloudFront / WAF / DDB / S3）→ Base zip → Meta-Agent → 前端 build 与同步 → CloudFront invalidation。重复执行时只更新有变化的部分。

### 常用子命令

```bash
bash scripts/deploy-all.sh --only-agent       # 仅更新 Meta-Agent
bash scripts/deploy-all.sh --only-frontend    # 仅更新前端
bash scripts/deploy-all.sh --skip-frontend    # 基础设施 + Meta-Agent
bash scripts/deploy-all.sh --dry-run          # 预检 + cdk diff

bash scripts/build-mcp.sh                     # 构建 MCP Docker 镜像
bash scripts/deploy-mcp.sh                    # 部署 MCP targets（按 region 自动过滤不可用项）

cd frontend && npm run dev                    # 本地开发
```

### 常见问题

- **`AGENT_STUDIO_REGION is not set`** — Vite 不再静默 fallback，必须在 `.env` 里显式写明
- **Docker not running** — CDK 打包 Lambda 需要 Docker daemon；`--only-frontend` / `--only-agent` / `--dry-run` 不依赖 Docker

## 项目结构

```
agent-studio/
├── frontend/              # React 19 + Vite 8 + Tailwind 4 + Zustand 5
├── meta-agent/            # Meta-Agent on AgentCore Runtime（Kiro CLI 驱动）
│   ├── kiro_adapter/      # Kiro 胶水层（ACP 驱动、stdio MCP、SSE mapper）
│   ├── tools/             # Meta 工具集（Agent 生命周期、skill、MCP、schedule、secret、link 等）
│   ├── tools_library/     # 预构建 Agent 工具模板（web_search、s3_read、chart 等）
│   └── templates/         # 代码生成 + prompt 模板
├── lambda/
│   ├── crud/              # Python CRUD Lambda（含 kiro_key.py 管理 key + usage）
│   ├── invoke-node/       # Node.js SSE streaming proxy
│   └── a2a-proxy/         # A2A JSON-RPC proxy
├── mcp-runtime/           # AWS MCP 目录接入（mcp-registry.yaml 控制启用项）+ Dockerfile
├── infra/                 # AWS CDK (TypeScript)
├── scripts/               # 部署 / 测试脚本
└── .env.example
```

## 测试

```bash
bash scripts/run-tests.sh
```

---

# Agent Studio (English)

An agent orchestration platform on AWS Bedrock AgentCore. Describe what you need in natural language and the Meta-Agent creates, deploys, and maintains the agent for you. The Meta-Agent is powered by the Kiro CLI; agents run on Strands.

## What It Does

- **Conversational agent creation** — Tell the Meta-Agent what you need; it writes the prompt, picks tools, generates the code, deploys.
- **Visualized debugging** — Streaming responses with every tool call's input and output surfaced in the chat stream.
- **Agent-to-agent** — `link_agent` mounts one agent as another's tool over A2A; credentials are provisioned automatically.
- **Knowledge Base (RAG)** — Create KBs via chat, upload documents for automatic vectorization (S3 Vectors + Cohere Multilingual v3), agents retrieve via `kb_retrieve` on demand.
- **Hot-swappable skills** — AgentSkills.io format, lazy-loaded at runtime, reusable across agents.
- **Multimodal input** — Images / PDF / Excel / CSV / TSV parsed out of the box, no extra setup.
- **Scheduled triggers** — Visual cron builder; every run is archived as a card with a click-through to full run details.
- **MCP toolbelt** — The full AWS-official MCP catalog, enable what you need; per-target IAM roles, least privilege.
- **Workspace IAM isolation** — Three-layer permission model (Permission Boundary ceiling → per-workspace role → target-level grants); Admin Console one-click grants with sensitivity badges and confirmation dialogs; `SimulatePrincipalPolicy` real-time checks with progress bars per target.
- **Cross-session memory** — Powered by AgentCore Memory: agents remember user preferences and facts across sessions and devices. Users can view and manage memories from the chat drawer.
- **Platform-level observability** — Trace timeline, latency percentiles, error rates, and token costs aggregated per agent and per workspace on a single screen.
- **Marketplace** — Publish and clone agents / skills / tools across workspaces; metadata is public, source stays private until cloned.

## Meta-Agent Capabilities

The Meta-Agent ships with 47 built-in tools covering the full agent lifecycle:

| Domain | Capabilities |
|--------|-------------|
| **Agent lifecycle** | Create, update, validate (`validate_agent`), deploy, archive, restore, permanently delete; supports both zip and harness runtimes |
| **Skill system** | Create, edit script files, import from URL/Markdown, attach to agents, sync updates across agents, delete |
| **Multi-agent orchestration** | A2A link/unlink, invoke other agents, create scheduled triggers |
| **Knowledge Base (RAG)** | Create KB, upload documents, check ingestion status, attach/detach from agents, delete |
| **MCP integration** | Browse 50+ AWS-official MCP servers, inspect target tool lists, workspace permission check before enabling |
| **Ops & debugging** | Live logs, invocation trace analysis, assembled code preview, agent secrets management, workspace permission audit |
| **Prompt engineering** | Auto-generate standards-compliant system prompts (constraint layering, anti-pattern prevention, tool–prompt sync checks) |
| **Built-in tool library** | Browse pre-built tool templates (web_search, s3_read, chart, …); prefer reuse over custom code |

All operations are triggered through natural-language conversation — no need to memorize tool names or parameters.

## Architecture

```mermaid
graph LR
    User((User)) --> Web[Web Console]
    Cron[Scheduled trigger]
    Web -->|chat| Meta[Meta-Agent<br/>build / manage]

    subgraph Agents
        direction TB
        A1[Agent A] -->|A2A| A2[Agent B]
        A2 ~~~ A3[Agent C]
    end

    Web -->|chat| Agents
    Meta -.deploy.-> Agents
    Cron -.fire.-> Agents
    Agents --> LLM[Bedrock LLMs]
    Agents --> Ext[Skills · Tools · MCP]
    Agents <-->|memory| Mem[AgentCore Memory]
    Agents -.observability.-> Obs[Traces · Evaluations<br/>Costs]
```

Full system diagram (CloudFront / Lambda / EventBridge / Evaluator, …) and key design notes live in [docs/architecture.md](docs/architecture.md).

## Security & Permissions

Agent Studio enforces a three-layer permission model on every workspace, enabling AWS resource access while preventing privilege escalation:

```
Permission Boundary (ceiling)
  └─ Workspace Role (identity policy)
       └─ Target Grants (on-demand)
```

- **Permission Boundary** — The `AgentStudioWorkspaceCeiling` managed policy caps the maximum privileges of any workspace role. Write operations, IAM mutations, and privilege escalation are explicitly denied
- **Per-workspace role** — `AgentStudio-ws-{id}` IAM roles are created on demand and bound to the Permission Boundary. Workspaces without a role can only use platform built-in tools (search, charts, code interpreter)
- **Target-level grants** — Admins grant permissions per MCP target (e.g. CloudWatch, DynamoDB, IAM) from the Admin Console. IAM policies are auto-generated from `mcp-registry.yaml` as the single source of truth, eliminating manual drift
- **Sensitivity labels & confirmation** — Each target is labeled low / medium / high sensitivity. Medium+ grants trigger a confirmation dialog showing risk reasons and the specific IAM actions being granted
- **Real-time checks** — `SimulatePrincipalPolicy` provides live simulation with progress bars and per-action granted/missing status. `validate_agent` automatically verifies MCP permissions at agent creation time

## Deployment

### Prerequisites

- An AWS account with working credentials — `aws sts get-caller-identity` must succeed
- An IAM identity allowed to run `cdk bootstrap` and to create IAM roles, policies, and a Permission Boundary
- Local tooling: **Node.js 18+**, **Python 3.10+**, **Docker** (CDK Lambda bundling requires a running daemon), plus `npm`, `jq`, `yq` — the script preflight-checks all of them

### One-Click Deploy

First time? Two steps. Start with `.env`:

```bash
cp .env.example .env
```

Open `.env` and fill in three required fields:

- `AGENT_STUDIO_REGION` — `us-east-1` or `us-west-2`
- `AGENT_STUDIO_ACCOUNT_ID` — your 12-digit AWS account ID
- `ORIGIN_VERIFY_SECRET` — generate with `openssl rand -hex 32`

The rest (`META_AGENT_ID`, `COGNITO_*`, `CLOUDFRONT_*`) is filled in automatically by the deploy script.

Then deploy — 15–20 minutes end-to-end:

```bash
bash scripts/deploy-all.sh --region us-east-1   # or --region us-west-2
```

The script runs: CDK infra (Cognito / Lambda / CloudFront / WAF / DDB / S3) → base zip → Meta-Agent → frontend build & sync → CloudFront invalidation. Re-runs only update what actually changed.

### Common Subcommands

```bash
bash scripts/deploy-all.sh --only-agent       # Meta-Agent code only
bash scripts/deploy-all.sh --only-frontend    # Frontend only
bash scripts/deploy-all.sh --skip-frontend    # Infra + Meta-Agent
bash scripts/deploy-all.sh --dry-run          # Preflight + cdk diff

bash scripts/build-mcp.sh                     # Build MCP Docker images
bash scripts/deploy-mcp.sh                    # Deploy MCP targets (unavailable ones auto-filtered)

cd frontend && npm run dev                    # Local dev
```

### Troubleshooting

- **`AGENT_STUDIO_REGION is not set`** — Vite no longer silently falls back; set it explicitly in `.env`.
- **Docker not running** — CDK Lambda bundling needs a running Docker daemon. `--only-frontend` / `--only-agent` / `--dry-run` do not.

## Project Structure

```
agent-studio/
├── frontend/              # React 19 + Vite 8 + Tailwind 4 + Zustand 5
├── meta-agent/            # Meta-Agent on AgentCore Runtime (Kiro-driven)
│   ├── kiro_adapter/      # Kiro glue (ACP driver, stdio MCP, SSE mapper)
│   ├── tools/             # Meta tools (agent lifecycle, skills, MCP, schedule, secrets, link, ...)
│   ├── tools_library/     # Pre-built agent tool templates (web_search, s3_read, chart, ...)
│   └── templates/         # Codegen + prompt templates
├── lambda/
│   ├── crud/              # Python CRUD Lambda (includes kiro_key.py — key + usage)
│   ├── invoke-node/       # Node.js SSE streaming proxy
│   └── a2a-proxy/         # A2A JSON-RPC proxy
├── mcp-runtime/           # AWS MCP catalog integration (mcp-registry.yaml gates what's enabled) + Dockerfile
├── infra/                 # AWS CDK (TypeScript)
├── scripts/               # Deploy + test scripts
└── .env.example
```

## Testing

```bash
bash scripts/run-tests.sh
```
