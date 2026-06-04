import type { Meta, StoryObj } from "@storybook/react-vite";
import AgentCostsSection from "../AgentCostsSection";

/**
 * AgentCostsSection fetches cost/usage data for an agent via fetchAgentCosts.
 *
 * Dependencies:
 * - fetchAgentCosts (API call) — without a backend the component will show
 *   loading then error state, demonstrating those branches naturally.
 *
 * In Storybook the API call will fail (no backend), exercising the error path.
 */

const meta: Meta<typeof AgentCostsSection> = {
  title: "Agents/AgentCostsSection",
  component: AgentCostsSection,
  args: {
    agentId: "agt-cost-demo-001",
  },
  decorators: [
    (Story) => (
      <div className="max-w-md mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof AgentCostsSection>;

/** Default: triggers the API call which will show loading then error/empty. */
export const Default: Story = {};

/** Different agent ID to verify the component is parameterized. */
export const DifferentAgent: Story = {
  args: {
    agentId: "agt-analytics-xyz789",
  },
};

/** Shows the component with a short agent ID for visual variance. */
export const ShortId: Story = {
  args: {
    agentId: "a1",
  },
};
