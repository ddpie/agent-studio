import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import RunDetailModal from "../RunDetailModal";

/**
 * RunDetailModal is a full-screen modal that fetches and displays run details.
 *
 * Dependencies:
 * - useRunDetail hook (API fetch for run detail + output)
 *
 * Without a backend the hook will show loading → error state inside the
 * modal shell, which exercises those branches. The modal chrome (header,
 * close button, backdrop) renders regardless of data state.
 */

const meta: Meta<typeof RunDetailModal> = {
  title: "Agents/RunDetailModal",
  component: RunDetailModal,
  args: {
    agentId: "agt-run-modal-001",
    runId: "run-abc123def456",
    onClose: fn(),
  },
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj<typeof RunDetailModal>;

/** Default: triggers the hook which shows loading then error inside the modal. */
export const Default: Story = {};

/** Different run ID to verify parameterization. */
export const AnotherRun: Story = {
  args: {
    agentId: "agt-daily-reporter",
    runId: "run-xyz789",
  },
};

/** Short IDs edge case. */
export const ShortIds: Story = {
  args: {
    agentId: "a1",
    runId: "r1",
  },
};
