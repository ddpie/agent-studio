import type { Meta, StoryObj } from "@storybook/react-vite";
import SecretsSection from "../SecretsSection";
import { useWorkspaceStore } from "../../../stores/workspace-store";

/**
 * SecretsSection renders a compact inline secrets manager within the agent
 * edit form. It lists existing secrets (fetched via listAgentSecrets) and
 * allows adding/deleting key-value pairs.
 *
 * Dependencies:
 * - listAgentSecrets / putAgentSecret / deleteAgentSecret (API calls)
 * - useWorkspaceStore (role-based permission gating)
 * - shared/Section (presentational wrapper)
 *
 * Without a backend the component will show a loading spinner then an error
 * or empty state. We pre-set the workspace store role to demonstrate
 * permission variants.
 */

const meta: Meta<typeof SecretsSection> = {
  title: "Agents/SecretsSection",
  component: SecretsSection,
  args: {
    agentId: "agt-test-secrets-001",
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
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof SecretsSection>;

/** Default: admin role, triggers API fetch (shows loading then empty/error). */
export const Default: Story = {};

/** Viewer role: mutation affordances are hidden. */
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
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

/** Owner role: full access to manage secrets. */
export const OwnerRole: Story = {
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "owner",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      return (
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};
