import type { Meta, StoryObj } from "@storybook/react-vite";
import TracesTab from "../TracesTab";

/**
 * TracesTab lists trace sessions for an agent with expandable detail
 * rows that show RunDetail with token usage and tool calls.
 *
 * Dependencies:
 * - listTraces / getSessionTrace (API calls)
 * - RunDetail (presentational sub-component)
 *
 * Without a backend the component will show the empty state after
 * the initial fetch returns no sessions (or error is caught).
 */

const meta: Meta<typeof TracesTab> = {
  title: "Agents/TracesTab",
  component: TracesTab,
  args: {
    agentId: "agt-abc123def456",
    range: "24h",
  },
  decorators: [
    (Story) => (
      <div className="max-w-4xl mx-auto h-[500px] border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900 overflow-hidden">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof TracesTab>;

/** Default: 24h range. Will show loading then empty/error without backend. */
export const Default: Story = {};

/** Short range: 1h window. */
export const OneHourRange: Story = {
  args: {
    range: "1h",
  },
};

/** Week range: 7d window. */
export const WeekRange: Story = {
  args: {
    range: "7d",
  },
};
