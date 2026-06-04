import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import StatusBadge from "../StatusBadge";

const meta: Meta<typeof StatusBadge> = {
  title: "Common/StatusBadge",
  component: StatusBadge,
  decorators: [
    (Story) => (
      <div className="flex items-center gap-4 p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof StatusBadge>;

export const Default: Story = {
  args: { status: "READY" },
};

export const AllStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-3">
      <StatusBadge status="READY" />
      <StatusBadge status="CREATING" />
      <StatusBadge status="UPDATING" />
      <StatusBadge status="DELETING" />
      <StatusBadge status="CREATE_FAILED" />
      <StatusBadge status="UPDATE_FAILED" />
      <StatusBadge status="UNKNOWN" />
      <StatusBadge status={null} />
    </div>
  ),
};

export const Compact: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <StatusBadge status="READY" compact />
      <StatusBadge status="CREATING" compact />
      <StatusBadge status="CREATE_FAILED" compact />
      <StatusBadge status="UPDATING" compact />
    </div>
  ),
};

export const Interactive: Story = {
  args: {
    status: "READY",
    onClick: fn(),
  },
};

export const CompactInteractive: Story = {
  args: {
    status: "CREATE_FAILED",
    compact: true,
    onClick: fn(),
  },
};
