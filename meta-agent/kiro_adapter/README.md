# kiro_adapter

Replaces the Strands-based Meta-Agent loop (`main.py`) with Kiro CLI as the
reasoning backbone, while reusing the existing 25 Meta-Agent tools by exposing
them over a local HTTP MCP server.

## Architecture

```
Browser (React, unchanged)
    ↓ /invoke/meta-agent (SSE, unchanged)
CloudFront + Invoke Lambda (unchanged)
    ↓
AgentCore Runtime  (per-invocation instance)
├── main.py (rewritten)
│    │
│    ├── 1. Start local MCP server on 127.0.0.1:<port>
│    │      Exposes the 25 existing @tool functions as MCP tools.
│    │
│    ├── 2. Materialize a per-invocation KIRO_HOME at /tmp/kiro_home_<uuid>
│    │      ├─ .kiro/agents/meta-agent.json   (custom agent config)
│    │      ├─ prompts/meta-agent.md          (the 342-line system prompt)
│    │      └─ .kiro/settings/mcp.json        (points at local HTTP MCP)
│    │
│    ├── 3. Spawn `kiro-cli acp --agent meta-agent --model <...>`
│    │      (KIRO_API_KEY via env, HOME overridden)
│    │
│    ├── 4. ACP client drives: initialize → session/new → session/prompt
│    │
│    └── 5. ACP notifications → existing SSE event protocol
│           AgentMessageChunk → raw text
│           ToolCall          → {"__tool": "start", "name": ...}
│           ToolCallUpdate    → (optional progress)
│           ToolCall output   → {"__tool": "result", ...}
│           TurnEnd           → {"__tool": "end"}
│
└── Egress: Kiro backend (*.kiro.dev) + AWS APIs (IAM role)
```

## Files

- `mcp_server.py` — FastMCP HTTP server wrapping `tools/*.py`
- `acp_client.py` — JSON-RPC client over stdio to `kiro-cli acp`
- `sse_mapper.py` — ACP notifications → Agent Studio SSE event format
- `kiro_home.py` — Build per-invocation KIRO_HOME with agent/prompt/mcp config
- `__init__.py` — package marker

## Why Kiro B-mode (keep built-ins)

Per brainstorm decision: we keep Kiro's built-in tools (`use_aws`, `grep`,
`subagent`, `web_search`, etc.) alongside our MCP tools. This gives Meta-Agent
capabilities the old Strands loop lacked:
- live web search (for user-cited docs like 飞书 OpenAPI)
- parallel subagent orchestration (for batch operations)
- direct AWS CLI access (for diagnostics)

## Kiro binary provisioning

Kiro CLI is **not** committed. It is downloaded fresh on every deploy from
`https://prod.download.cli.kiro.dev/stable/latest/kirocli-aarch64-linux.zip`
by `scripts/build-base-zip.sh`, with sha256 verification against the official
manifest. See the build script for the download/verify flow.
