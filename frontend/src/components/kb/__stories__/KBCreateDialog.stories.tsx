import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import KBCreateDialog from "../KBCreateDialog";

const meta: Meta<typeof KBCreateDialog> = {
  title: "KB/KBCreateDialog",
  component: KBCreateDialog,
  args: {
    open: true,
    onClose: fn(),
    onCreate: fn().mockResolvedValue("kb-new-123"),
  },
};

export default meta;
type Story = StoryObj<typeof KBCreateDialog>;

export const Default: Story = {};

export const Closed: Story = {
  args: { open: false },
};

export const SlowCreate: Story = {
  args: {
    onCreate: fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve("kb-slow-456"), 3000))
    ),
  },
};

export const CreateError: Story = {
  args: {
    onCreate: fn().mockRejectedValue(new Error("Knowledge base name already exists")),
  },
};
