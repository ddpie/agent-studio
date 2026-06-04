import type { Meta, StoryObj } from "@storybook/react-vite";
import { Settings } from "lucide-react";
import Section from "../shared/Section";

const meta: Meta<typeof Section> = {
  title: "Agents/Section",
  component: Section,
};

export default meta;
type Story = StoryObj<typeof Section>;

export const Default: Story = {
  args: {
    title: "Configuration",
    children: <p className="text-sm text-gray-600 dark:text-gray-300">Section content goes here.</p>,
  },
};

export const WithIcon: Story = {
  args: {
    title: "Settings",
    icon: <Settings className="w-3 h-3" />,
    children: <p className="text-sm text-gray-600 dark:text-gray-300">Content with icon header.</p>,
  },
};

export const WithAction: Story = {
  args: {
    title: "Tools",
    action: <button className="text-[10px] text-blue-600 hover:underline">+ Add</button>,
    children: <p className="text-sm text-gray-600 dark:text-gray-300">Content with action button.</p>,
  },
};
