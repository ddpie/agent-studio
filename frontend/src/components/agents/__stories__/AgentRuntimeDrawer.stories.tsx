import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import AgentRuntimeDrawer from "../AgentRuntimeDrawer";

/**
 * AgentRuntimeDrawer shows agent runtime status in a slide-over panel.
 *
 * The `useRuntimeStatus` hook fetches from the API. In Storybook it will
 * naturally hit network errors which surfaces the error state. We rely on
 * the hook returning { data: null, error, loading } for offline stories.
 *
 * For richer mocking we'd need to intercept the API — these stories
 * demonstrate layout structure and the closed/loading/error states that
 * occur naturally without a backend.
 */

const meta: Meta<typeof AgentRuntimeDrawer> = {
  title: "Agents/AgentRuntimeDrawer",
  component: AgentRuntimeDrawer,
  args: {
    agentId: "agt-abc123def456",
    onClose: fn(),
  },
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj<typeof AgentRuntimeDrawer>;

/** Default: renders with a real agentId. The hook will attempt to fetch
 *  and display either loading or error state depending on network. */
export const Default: Story = {};

/** Null agentId: drawer renders nothing (early return). */
export const NullAgent: Story = {
  args: {
    agentId: null,
  },
};
