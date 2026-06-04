import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import SkillToolbar from "../SkillToolbar";

const meta: Meta<typeof SkillToolbar> = {
  title: "Pages/SkillToolbar",
  component: SkillToolbar,
  args: {
    skill: {
      id: "skill-abc123",
      name: "web-search",
      description: "Search the web for real-time information",
    },
    isAgentMode: false,
    hasPendingOps: false,
    pendingCount: 0,
    saving: false,
    validating: false,
    assistantOpen: false,
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
type Story = StoryObj<typeof SkillToolbar>;

export const Default: Story = {};

export const WithPendingChanges: Story = {
  args: {
    hasPendingOps: true,
    pendingCount: 3,
  },
};

export const Saving: Story = {
  args: {
    hasPendingOps: true,
    pendingCount: 2,
    saving: true,
  },
};

export const AgentMode: Story = {
  args: {
    isAgentMode: true,
    skill: {
      id: "skill-xyz789",
      name: "data-analyzer",
      description: "Analyze datasets and generate insights",
    },
  },
};

export const AssistantOpen: Story = {
  args: {
    assistantOpen: true,
  },
};

export const Validating: Story = {
  args: {
    validating: true,
  },
};
