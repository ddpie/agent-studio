import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import McpTargetSelector from "../McpTargetSelector";

/**
 * McpTargetSelector lets users pick MCP targets (managed tool servers)
 * to attach to an agent. It fetches the available target list on mount
 * via `apiGet("/mcp/targets")` and checks workspace permissions.
 *
 * Dependencies:
 * - apiGet (API call for target list and tool details)
 * - getWorkspacePermissions / getWorkspaceId (IAM permission check)
 *
 * Without a backend the loading spinner resolves to an empty target list.
 * The "legacy config" prop path can be demonstrated statically.
 */

const meta: Meta<typeof McpTargetSelector> = {
  title: "Agents/McpTargetSelector",
  component: McpTargetSelector,
  args: {
    selectedTargets: [],
    onChange: fn(),
  },
  decorators: [
    (Story) => (
      <div className="max-w-md mx-auto p-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof McpTargetSelector>;

/** Default: no targets selected. Shows the add button after loading. */
export const Default: Story = {};

/** With pre-selected targets (names must match what the API would return). */
export const WithSelected: Story = {
  args: {
    selectedTargets: ["cloudwatch", "s3-readonly", "lambda"],
  },
};

/** Legacy config mode: shows a warning banner instead of the selector. */
export const LegacyConfig: Story = {
  args: {
    hasLegacyConfig: true,
    selectedTargets: [],
  },
};

/** With mcp- prefixed targets (tests normalization logic). */
export const PrefixedTargets: Story = {
  args: {
    selectedTargets: ["mcp-cloudwatch", "mcp-rds"],
  },
};
