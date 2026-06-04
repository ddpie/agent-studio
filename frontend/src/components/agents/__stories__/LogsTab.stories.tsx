import type { Meta, StoryObj } from "@storybook/react-vite";
import LogsTab from "../LogsTab";

/**
 * LogsTab displays a filterable, paginated log viewer with level badges,
 * timestamps, follow mode, and a CloudWatch deep link.
 *
 * Dependencies:
 * - fetchAgentLogs (API call from api-client)
 * - agentConfig.region (compile-time constant)
 *
 * Without a backend, the component will show the error state after the
 * initial fetch fails. This still demonstrates the toolbar layout, filter
 * controls, and empty/error states.
 */

const meta: Meta<typeof LogsTab> = {
  title: "Agents/LogsTab",
  component: LogsTab,
  args: {
    agentId: "agt-abc123def456",
  },
  decorators: [
    (Story) => (
      <div className="max-w-4xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof LogsTab>;

/** Default: shows toolbar with filters; fetch will error without backend. */
export const Default: Story = {};

/** Different agent to confirm parameterization. */
export const AnotherAgent: Story = {
  args: {
    agentId: "agt-prod-789",
  },
};
