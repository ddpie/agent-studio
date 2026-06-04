import type { Meta, StoryObj } from "@storybook/react-vite";
import RunListItem from "../RunListItem";

const meta: Meta<typeof RunListItem> = {
  title: "Agents/RunListItem",
  component: RunListItem,
  decorators: [(Story) => <ul className="max-w-sm"><Story /></ul>],
};

export default meta;
type Story = StoryObj<typeof RunListItem>;

const baseRun = {
  runId: "run-001",
  agentId: "agent-1",
  input: "Analyze Q4 revenue data and generate a summary report",
  status: "completed" as const,
  startedAt: new Date(Date.now() - 120_000).toISOString(),
  completedAt: new Date(Date.now() - 115_500).toISOString(),
  durationMs: 4500,
  totalTokens: 12340,
  artifactCount: 0,
  trigger: "manual" as const,
  scheduleId: null,
  model: "anthropic.claude-sonnet-4-6",
};

export const Completed: Story = {
  args: { run: baseRun, selected: false, onSelect: () => {} },
};

export const Selected: Story = {
  args: { run: baseRun, selected: true, onSelect: () => {} },
};

export const Running: Story = {
  args: {
    run: { ...baseRun, runId: "run-002", status: "running", durationMs: null, totalTokens: null, completedAt: null },
    selected: false,
    onSelect: () => {},
  },
};

export const Failed: Story = {
  args: {
    run: { ...baseRun, runId: "run-003", status: "failed", input: "Deploy new model version" },
    selected: false,
    onSelect: () => {},
  },
};

export const Scheduled: Story = {
  args: {
    run: {
      ...baseRun,
      runId: "run-004",
      trigger: "schedule",
      scheduleId: "agent-studio-ws1-daily-report",
      input: "Generate daily analytics report",
    },
    selected: false,
    onSelect: () => {},
  },
};

export const WithArtifacts: Story = {
  args: {
    run: { ...baseRun, runId: "run-005", artifactCount: 3 },
    selected: false,
    onSelect: () => {},
  },
};
