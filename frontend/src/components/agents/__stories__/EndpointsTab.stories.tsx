import type { Meta, StoryObj } from "@storybook/react-vite";
import EndpointsTab from "../EndpointsTab";

/**
 * EndpointsTab manages agent runtime endpoints (create, switch version, delete).
 *
 * Dependencies:
 * - useRuntimeEndpoints hook (API fetch)
 * - useRuntimeVersions hook (API fetch)
 * - EndpointDialog, ConfirmDialog (presentational children)
 * - createAgentEndpoint, updateAgentEndpoint, deleteAgentEndpoint (API)
 *
 * Without a backend, hooks show loading → error state, which demonstrates
 * the error and empty-state branches naturally.
 */

const meta: Meta<typeof EndpointsTab> = {
  title: "Agents/EndpointsTab",
  component: EndpointsTab,
  args: {
    agentId: "agt-endpoint-demo-001",
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
type Story = StoryObj<typeof EndpointsTab>;

/** Default: triggers hooks which show loading then error/empty. */
export const Default: Story = {};

/** Different agent ID shows the component is parameterized. */
export const AnotherAgent: Story = {
  args: {
    agentId: "agt-prod-service-xyz",
  },
};
