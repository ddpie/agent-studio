import type { Meta, StoryObj } from "@storybook/react-vite";
import Field from "../shared/Field";

const meta: Meta<typeof Field> = {
  title: "Agents/Field",
  component: Field,
};

export default meta;
type Story = StoryObj<typeof Field>;

export const Default: Story = {
  args: {
    label: "Agent Name",
    children: (
      <input
        type="text"
        defaultValue="myDataAnalyzer"
        className="w-full px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-700 dark:text-gray-100"
      />
    ),
  },
};

export const WithHint: Story = {
  args: {
    label: "Model ID",
    hint: "Only alphanumeric characters allowed, max 36 chars",
    children: (
      <input
        type="text"
        defaultValue="anthropic.claude-sonnet-4-6"
        className="w-full px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-700 dark:text-gray-100"
      />
    ),
  },
};

export const Changed: Story = {
  args: {
    label: "System Prompt",
    changed: true,
    children: (
      <textarea
        defaultValue="You are a helpful assistant."
        className="w-full px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-700 dark:text-gray-100"
        rows={3}
      />
    ),
  },
};

export const WithOptimize: Story = {
  args: {
    label: "System Prompt",
    onOptimize: () => alert("AI optimize triggered"),
    children: (
      <textarea
        defaultValue="You are a helpful assistant."
        className="w-full px-2 py-1 text-sm border rounded dark:bg-gray-900 dark:border-gray-700 dark:text-gray-100"
        rows={3}
      />
    ),
  },
};
