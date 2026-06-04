import type { Meta, StoryObj } from "@storybook/react-vite";
import EvaluationsTab from "../EvaluationsTab";

/**
 * EvaluationsTab displays agent evaluation scores with per-evaluator breakdown.
 *
 * Dependencies:
 * - useAgentEvaluations hook (API fetch)
 * - getAgentEvaluationStatus (API call)
 * - enableAgentEvaluations (API call)
 *
 * Without a backend the component will show loading → error or the "not
 * enabled" empty state depending on how the API calls resolve.
 */

const meta: Meta<typeof EvaluationsTab> = {
  title: "Agents/EvaluationsTab",
  component: EvaluationsTab,
  args: {
    agentId: "agt-eval-demo-001",
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
type Story = StoryObj<typeof EvaluationsTab>;

/** Default: triggers the hook which will show loading then error/empty. */
export const Default: Story = {};

/** Another agent ID for parameterization check. */
export const AnotherAgent: Story = {
  args: {
    agentId: "agt-quality-checker-789",
  },
};

/** Short ID edge case. */
export const ShortId: Story = {
  args: {
    agentId: "a1",
  },
};
