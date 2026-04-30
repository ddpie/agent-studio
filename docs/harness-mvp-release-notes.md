# Harness MVP v1 (2026-04-30)

Tag: `harness-mvp-v1` (merge commit `c653e8a` on `main`).

## What's new

Agent Studio now supports a second Agent runtime alongside the existing
**zip** runtime: **harness**, AWS Bedrock AgentCore's declarative
managed runtime (public preview). You pick the runtime once at create
time; it can't be changed later.

### When to pick which

| Runtime | Pick when you need… |
|---|---|
| **zip** | Custom Python tools, MCP targets, skills, A2A linking, browser / code interpreter |
| **harness** | Reliable prompt + model + memory agent, fastest time-to-ready, no custom code |

Harness MVP is intentionally minimal: prompt, model, and optional long-term
memory. It does **not** support MCP, custom tools, skills, or linked
agents — if you need any of those, pick zip. The Meta-Agent knows this
and will steer you toward the right runtime.

### Why it matters

- **Create time ~2× faster** (median 16 s vs 31 s for zip).
- **No Meta-Agent code-gen step**, so the class of "LLM wrote broken
  Python" failures that occasionally hits zip agents can't happen for
  harness.
- **Memory is first-class**: flip the toggle on, and harness automatically
  reads/writes the workspace's memory resource on every invocation. No
  code to write on your end.

## How to use

- **From the Agent list form**: pick `harness (实验性)` in the 运行时
  dropdown, choose a model (required — harness binds to one model), fill
  prompt, create.
- **Via Meta-Agent**: say "帮我创建一个 harness agent …", Meta-Agent will
  ask for a model if you don't specify one, then propose a create-form
  card.
- **Chat**: works exactly like zip — same SSE streaming, same chat-page
  model switcher (you can override harness's default model per message).
- **Memory**: in the edit page, click 启用长期记忆. The toggle writes DDB
  and simultaneously calls UpdateHarness with the workspace memory arn.

## Known limitations

1. **No MCP tools**: AWS harness APIs expose `outboundAuth: awsIam` but
   don't actually sign requests to our MCP gateway (401), and their
   `remote_mcp` path has no SigV4 hook (403). We're blocked until AWS
   ships real implementations; `docs/harness-reliability-report.md` has
   root-cause details.
2. **No Python tools / skills / linked agents**: harness has no code
   pipeline to hook into. These sections are hidden in the edit form for
   harness agents.
3. **Memory disable is one-way**: once you enable memory, you can't clear
   the memory arn via UpdateHarness (AWS API has no legal payload for
   that). Toggling off stops the frontend from reading the memory drawer,
   but the harness container keeps the arn until the agent is deleted.
4. **No versions / endpoints**: AgentCore Harness doesn't have
   `CreateHarnessEndpoint`. Detail-page tabs for deployments, endpoints,
   and runtime logs are hidden for harness agents.

## How to roll back

If harness causes a production issue:

```bash
# 1. Roll main back to pre-merge state
git checkout main
git reset --hard pre-harness-mvp-merge    # git tag
git push origin main --force-with-lease

# 2. Re-deploy the stack
cd infra && npx cdk deploy AgentStudioStack --require-approval never

# 3. Re-deploy Meta-Agent so SYSTEM_PROMPT no longer mentions harness
bash scripts/deploy-agentcore.sh

# 4. Re-deploy frontend (hides runtime dropdown)
bash scripts/deploy-all.sh --only-frontend
```

Rollback impact: existing harness agents in DDB will show a broken detail
page (their `runtime_type=harness` field will no longer be understood),
but they won't be deleted. Manual cleanup if you want them gone:

```bash
aws dynamodb scan --table-name agent-studio-agents \
  --filter-expression 'runtime_type = :r' \
  --expression-attribute-values '{":r":{"S":"harness"}}' \
  --region us-east-1 \
  --query 'Items[].{id:agentId.S,arn:harness_arn.S}'
# For each: aws bedrock-agentcore-control delete-harness --harness-id <id> --region us-east-1
# Then: aws dynamodb delete-item --table-name agent-studio-agents --key '{"agentId":{"S":"<id>"}}' --region us-east-1
```

## What was fixed during test

Four bugs surfaced during E2E verification and were fixed in-branch
before merging (see commit history on merge `c653e8a`):

1. UpdateHarness memory shape required `optionalValue` wrapper —
   previously sent flat, caused ParamValidationError (commit `fe94380`).
2. Workspace role + ceiling boundary missing memory data-plane actions
   (`ListEvents`, `CreateEvent`, `RetrieveMemoryRecords`); memory-enabled
   harness invocations returned AccessDenied (commits `6ceb174`,
   `84c3167`).
3. `agentCoreGateway` field name was camelCase in SDK shape but we
   emitted `agentcoreGateway` (commit `be223d3`; now irrelevant because
   MCP wiring was ultimately reverted).
4. `_agent_response` didn't expose `system_prompt`; harness agents loaded
   with empty prompt textarea in edit page (commit `cbae4b0`).

## Verified via

- 6 Playwright E2E scenarios — screenshots under
  `.claude/screenshots/e2e-round2/`.
- AWS-layer reliability comparison — `docs/harness-reliability-report.md`.
- Manual chat + model override + memory toggle end-to-end after deploy.
