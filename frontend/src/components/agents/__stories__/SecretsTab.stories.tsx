import type { Meta, StoryObj } from "@storybook/react-vite";
import SecretsTab from "../SecretsTab";
import { useWorkspaceStore } from "../../../stores/workspace-store";

/**
 * SecretsTab is a full-page tab for managing agent secrets with a table
 * view, create modal, and delete confirmation dialog.
 *
 * Dependencies:
 * - listAgentSecrets / putAgentSecret / deleteAgentSecret (API calls)
 * - useWorkspaceStore (role-based gating for admin/owner)
 * - ConfirmDialog (presentational)
 *
 * Without a backend the hook fires, yielding a loading state then error/empty.
 */

const meta: Meta<typeof SecretsTab> = {
  title: "Agents/SecretsTab",
  component: SecretsTab,
  args: {
    agentId: "agt-secrets-tab-001",
  },
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "admin",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      return (
        <div className="max-w-2xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof SecretsTab>;

/** Default: admin with API loading state. */
export const Default: Story = {};

/** Viewer: create button hidden, delete buttons hidden. */
export const ViewerRole: Story = {
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "viewer",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      return (
        <div className="max-w-2xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

/** Editor role: still cannot manage secrets (admin-only). */
export const EditorRole: Story = {
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "editor",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      return (
        <div className="max-w-2xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};
