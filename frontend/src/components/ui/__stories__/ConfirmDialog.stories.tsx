import type { Meta, StoryObj } from "@storybook/react-vite";
import ConfirmDialog from "../ConfirmDialog";

const meta: Meta<typeof ConfirmDialog> = {
  title: "UI/ConfirmDialog",
  component: ConfirmDialog,
  args: {
    open: true,
    title: "Delete Agent",
    message: "This action cannot be undone. The agent and all its deployments will be permanently removed.",
    onConfirm: () => {},
    onCancel: () => {},
  },
};

export default meta;
type Story = StoryObj<typeof ConfirmDialog>;

export const Default: Story = {};

export const Danger: Story = {
  args: { danger: true, confirmLabel: "Delete", title: "Permanently Delete" },
};

export const CustomLabels: Story = {
  args: { confirmLabel: "Yes, proceed", cancelLabel: "Go back" },
};

export const Closed: Story = {
  args: { open: false },
};
