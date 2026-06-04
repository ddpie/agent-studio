import type { Meta, StoryObj } from "@storybook/react-vite";
import StatsStrip from "../StatsStrip";

const meta: Meta<typeof StatsStrip> = {
  title: "Agents/StatsStrip",
  component: StatsStrip,
  parameters: {
    mockData: [
      {
        url: "/api/workspaces/*/agents/agent-1/traces/stats*",
        method: "GET",
        status: 200,
        response: {
          count: 1234,
          errorRate: 0.023,
          latencyMs: { avg: 850, p50: 720, p95: 2100, p99: 4500 },
          timeseries: [
            { count: 10, p95Ms: 1200 },
            { count: 15, p95Ms: 900 },
            { count: 8, p95Ms: 1500 },
            { count: 22, p95Ms: 800 },
            { count: 18, p95Ms: 1100 },
          ],
        },
      },
    ],
  },
};

export default meta;
type Story = StoryObj<typeof StatsStrip>;

export const Default: Story = {
  args: { agentId: "agent-1", range: "7d" },
};

export const HighErrorRate: Story = {
  args: { agentId: "agent-2", range: "24h" },
  parameters: {
    mockData: [
      {
        url: "/api/workspaces/*/agents/agent-2/traces/stats*",
        method: "GET",
        status: 200,
        response: {
          count: 500,
          errorRate: 0.12,
          latencyMs: { avg: 3200, p50: 2800, p95: 8500, p99: 12000 },
          timeseries: [
            { count: 50, p95Ms: 5000 },
            { count: 45, p95Ms: 7000 },
            { count: 60, p95Ms: 9000 },
          ],
        },
      },
    ],
  },
};
