import type { Meta, StoryObj } from "@storybook/react-vite";
import DeploymentsTab from "../DeploymentsTab";

/**
 * DeploymentsTab displays a table of agent runtime versions with status
 * badges and CloudWatch log links.
 *
 * Dependencies:
 * - useRuntimeVersions hook (fetches from API)
 * - StatusBadge (presentational, no external deps)
 *
 * In Storybook without a backend the hook will trigger the error or
 * empty state, demonstrating those layout paths naturally.
 */

const meta: Meta<typeof DeploymentsTab> = {
  title: "Agents/DeploymentsTab",
  component: DeploymentsTab,
  args: {
    agentId: "agt-abc123def456",
  },
  decorators: [
    (Story) => (
      <div className="max-w-3xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof DeploymentsTab>;

/** Default: triggers the hook which will show loading then error/empty. */
export const Default: Story = {};

/** Different agent ID to verify the component is parameterized. */
export const AnotherAgent: Story = {
  args: {
    agentId: "agt-xyz789",
  },
};
