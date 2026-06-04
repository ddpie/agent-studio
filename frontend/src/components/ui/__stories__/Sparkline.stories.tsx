import type { Meta, StoryObj } from "@storybook/react-vite";
import Sparkline from "../Sparkline";

const meta: Meta<typeof Sparkline> = {
  title: "UI/Sparkline",
  component: Sparkline,
  args: { width: 120, height: 32 },
};

export default meta;
type Story = StoryObj<typeof Sparkline>;

export const Ascending: Story = {
  args: { data: [2, 4, 6, 10, 14, 18, 22, 30] },
};

export const Volatile: Story = {
  args: { data: [10, 3, 15, 8, 20, 5, 18, 12, 25, 7] },
};

export const SinglePoint: Story = {
  args: { data: [5] },
};

export const Empty: Story = {
  args: { data: [] },
};

export const CustomColor: Story = {
  args: { data: [5, 10, 8, 12, 9, 15], color: "#ef4444" },
};
