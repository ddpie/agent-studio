import type { Meta, StoryObj } from "@storybook/react-vite";
import { MemoryRouter } from "react-router";
import KBAttachedAgents from "../KBAttachedAgents";

/**
 * KBAttachedAgents shows which agents are attached to a knowledge base,
 * with links to navigate to each agent's chat.
 *
 * Dependencies:
 * - useNavigate (React Router) — needs a Router provider
 * - useTranslation (i18next) — handled by Storybook preview
 */

const meta: Meta<typeof KBAttachedAgents> = {
  title: "KB/KBAttachedAgents",
  component: KBAttachedAgents,
  args: {
    agentIds: ["agt-abc123", "agt-def456", "agt-ghi789"],
    kbName: "Product Documentation",
  },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="max-w-md mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof KBAttachedAgents>;

/** Default: multiple agents attached. */
export const Default: Story = {};

/** No agents attached — shows empty message. */
export const Empty: Story = {
  args: {
    agentIds: [],
    kbName: "Empty KB",
  },
};

/** Single agent attached. */
export const SingleAgent: Story = {
  args: {
    agentIds: ["agt-single-001"],
    kbName: "API Reference",
  },
};
