import type { Meta, StoryObj } from "@storybook/react-vite";
import RunDetail from "../RunDetail";
import type { RunDetail as RunDetailType, RunOutput } from "../../../lib/runs-client";

const meta: Meta<typeof RunDetail> = {
  title: "Agents/RunDetail",
  component: RunDetail,
  decorators: [
    (Story) => (
      <div className="h-[600px] max-w-3xl mx-auto bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof RunDetail>;

const completedDetail: RunDetailType = {
  runId: "run-abc123",
  trigger: "schedule",
  scheduleId: "sched-daily-report",
  sessionId: "sess-xyz789",
  status: "completed",
  input: "Generate the daily customer support metrics report for 2026-06-03. Include ticket volume, average resolution time, and top issues.",
  outputUrl: "https://s3.us-east-1.amazonaws.com/bucket/runs/run-abc123/output.json",
  artifactRefs: [
    { key: "agents/agt-001/runs/run-abc123/daily-report.pdf", filename: "daily-report.pdf" },
    { key: "agents/agt-001/runs/run-abc123/metrics-chart.png", filename: "metrics-chart.png" },
  ],
  usage: { promptTokens: 1250, completionTokens: 890, totalTokens: 2140 },
  durationMs: 12450,
  model: "anthropic.claude-sonnet-4-20250514-v1:0",
  error: null,
  startedAt: "2026-06-03T08:00:00Z",
  completedAt: "2026-06-03T08:00:12Z",
};

const completedOutput: RunOutput = {
  text: "## Daily Support Metrics — June 3, 2026\n\n**Total Tickets:** 342 (+12% vs. last week)\n\n| Metric | Value |\n|--------|-------|\n| Avg Resolution Time | 2.4 hours |\n| First Response Time | 18 min |\n| CSAT Score | 4.6/5.0 |\n\n### Top Issues\n1. **Billing discrepancies** — 28% of tickets\n2. **Login failures** — 19% of tickets\n3. **API rate limits** — 14% of tickets\n\nReport saved as `daily-report.pdf`.",
  toolCalls: [
    {
      name: "fetch_zendesk_tickets",
      input: JSON.stringify({ date: "2026-06-03", status: "all" }, null, 2),
      output: JSON.stringify({ total: 342, resolved: 298, pending: 44 }, null, 2),
    },
    {
      name: "generate_chart",
      input: JSON.stringify({ type: "bar", metric: "tickets_by_category" }),
      output: "Chart generated and saved to S3.",
    },
  ],
};

const failedDetail: RunDetailType = {
  runId: "run-fail456",
  trigger: "manual",
  scheduleId: null,
  sessionId: "sess-def456",
  status: "failed",
  input: "Summarize all customer emails from the past week.",
  outputUrl: null,
  artifactRefs: [],
  usage: { promptTokens: 800, completionTokens: null, totalTokens: 800 },
  durationMs: 3200,
  model: "anthropic.claude-sonnet-4-20250514-v1:0",
  error: {
    code: "ToolExecutionError",
    message: "Tool 'fetch_emails' failed: Gmail API returned 403 Forbidden. The OAuth token has expired. Please re-authenticate via Settings > Integrations.",
  },
  startedAt: "2026-06-03T14:30:00Z",
  completedAt: "2026-06-03T14:30:03Z",
};

const runningDetail: RunDetailType = {
  runId: "run-running789",
  trigger: "schedule",
  scheduleId: "sched-weekly-digest",
  sessionId: null,
  status: "running",
  input: "Generate weekly performance digest for all deployed agents.",
  outputUrl: null,
  artifactRefs: [],
  usage: { promptTokens: null, completionTokens: null, totalTokens: null },
  durationMs: null,
  model: "anthropic.claude-sonnet-4-20250514-v1:0",
  error: null,
  startedAt: "2026-06-04T09:00:00Z",
  completedAt: null,
};

export const Completed: Story = {
  args: {
    detail: completedDetail,
    output: completedOutput,
    loading: false,
  },
};

export const Failed: Story = {
  args: {
    detail: failedDetail,
    output: null,
    loading: false,
  },
};

export const Running: Story = {
  args: {
    detail: runningDetail,
    output: null,
    loading: true,
  },
};

export const CompletedNoArtifacts: Story = {
  args: {
    detail: {
      ...completedDetail,
      artifactRefs: [],
      input: "What is the current agent deployment status?",
    },
    output: {
      text: "All 5 agents are currently in **READY** state. No deployments in progress.",
      toolCalls: [],
    },
    loading: false,
  },
};
