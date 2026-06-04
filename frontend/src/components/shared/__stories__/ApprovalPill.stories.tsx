import type { Meta, StoryObj } from "@storybook/react-vite";
import ApprovalPill from "../ApprovalPill";

const meta: Meta<typeof ApprovalPill> = {
  title: "Shared/ApprovalPill",
  component: ApprovalPill,
  decorators: [
    (Story) => (
      <div className="flex items-center gap-4 p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ApprovalPill>;

export const Approved: Story = {
  args: {
    type: "script",
    approved: true,
    size: "sm",
  },
};

export const Pending: Story = {
  args: {
    type: "script",
    approved: false,
    size: "sm",
  },
};

export const MediumSize: Story = {
  render: () => (
    <div className="flex items-center gap-4">
      <ApprovalPill type="script" approved={true} size="md" />
      <ApprovalPill type="script" approved={false} size="md" />
    </div>
  ),
};

export const PromptType: Story = {
  args: {
    type: "prompt",
    approved: false,
  },
  parameters: {
    docs: {
      description: { story: "Prompt-type skills render nothing (approval only applies to script skills)." },
    },
  },
};
