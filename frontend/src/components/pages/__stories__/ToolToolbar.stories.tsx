import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import ToolToolbar from "../ToolToolbar";

const meta: Meta<typeof ToolToolbar> = {
  title: "Pages/ToolToolbar",
  component: ToolToolbar,
  args: {
    toolName: "search_web",
    description: "Search the web using Brave Search API and return structured results",
    hasChanges: false,
    saving: false,
    validating: false,
    assistantOpen: false,
    readOnly: false,
    onBack: fn(),
    onSave: fn(),
    onDiscard: fn(),
    onValidate: fn(),
    onShowDiff: fn(),
    onDelete: fn(),
    onToggleAssistant: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof ToolToolbar>;

export const Default: Story = {};

export const WithChanges: Story = {
  args: {
    hasChanges: true,
  },
};

export const Saving: Story = {
  args: {
    hasChanges: true,
    saving: true,
  },
};

export const ReadOnly: Story = {
  args: {
    toolName: "builtin_read_file",
    description: "Built-in tool for reading files from the workspace",
    readOnly: true,
  },
};

export const ReadOnlyWithChanges: Story = {
  args: {
    toolName: "builtin_read_file",
    description: "Built-in tool for reading files from the workspace",
    readOnly: true,
    hasChanges: true,
  },
};

export const AssistantOpen: Story = {
  args: {
    assistantOpen: true,
  },
};
