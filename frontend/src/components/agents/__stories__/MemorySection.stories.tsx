import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import MemorySection from "../MemorySection";

/**
 * MemorySection lets users toggle agent memory and select strategies.
 *
 * Dependencies:
 * - updateAgent (API call for auto-save) — will fail without backend
 * - Section wrapper (presentational)
 *
 * This component accepts props directly (value + onChange), so we can
 * fully control its state via story args and a stateful decorator.
 */

const meta: Meta<typeof MemorySection> = {
  title: "Agents/MemorySection",
  component: MemorySection,
  args: {
    agentId: "agt-memory-demo-001",
    value: { enabled: false, strategies: [] },
    onChange: fn(),
    workspaceMemoryAvailable: true,
  },
  decorators: [
    (Story) => (
      <div className="max-w-md mx-auto p-4">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof MemorySection>;

/** Default: memory disabled, workspace memory available. */
export const Default: Story = {};

/** Memory enabled with some strategies selected. */
export const EnabledWithStrategies: Story = {
  args: {
    value: { enabled: true, strategies: ["userPreference", "semantic"] },
  },
};

/** All strategies selected. */
export const AllStrategies: Story = {
  args: {
    value: { enabled: true, strategies: ["userPreference", "semantic", "summary", "episodic"] },
  },
};

/** Workspace memory not available — checkboxes disabled with warning. */
export const WorkspaceMemoryUnavailable: Story = {
  args: {
    value: { enabled: false, strategies: [] },
    workspaceMemoryAvailable: false,
  },
};

/** Interactive wrapper that manages state for click-through testing. */
export const Interactive: Story = {
  render: function InteractiveMemory() {
    const [value, setValue] = useState({ enabled: true, strategies: ["semantic"] });
    return (
      <MemorySection
        agentId="agt-memory-interactive"
        value={value}
        onChange={setValue}
        workspaceMemoryAvailable={true}
      />
    );
  },
};
