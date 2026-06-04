import type { Meta, StoryObj } from "@storybook/react-vite";
import IntegrationTab from "../IntegrationTab";

/**
 * IntegrationTab shows A2A integration URLs and API key management.
 *
 * Dependencies:
 * - useA2aKeys hook (API fetch for keys list)
 * - getPublicAgentCardUrl / getA2aEndpointUrl (URL builders, no fetch)
 * - ConfirmDialog (presentational)
 *
 * Without a backend the hook will show loading → error for the keys section,
 * but URL boxes will render with their computed URLs.
 */

const meta: Meta<typeof IntegrationTab> = {
  title: "Agents/IntegrationTab",
  component: IntegrationTab,
  args: {
    agentId: "agt-integration-demo-001",
  },
  decorators: [
    (Story) => (
      <div className="max-w-2xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof IntegrationTab>;

/** Default agent integration view. */
export const Default: Story = {};

/** Meta-agent kind shows different URL patterns. */
export const MetaAgentKind: Story = {
  args: {
    agentId: "meta-agent",
    kind: "meta-agent",
  },
};

/** Another agent ID for visual differentiation. */
export const AnotherAgent: Story = {
  args: {
    agentId: "agt-prod-api-xyz",
  },
};
