import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import ChatInput from "../ChatInput";

const meta: Meta<typeof ChatInput> = {
  title: "Chat/ChatInput",
  component: ChatInput,
  args: {
    onSend: fn(),
    onCancel: fn(),
    isStreaming: false,
    imagesAllowed: true,
    selectedModel: "anthropic.claude-sonnet-4-20250514-v1:0",
    inputHeight: 60,
    dragHandleProps: { onMouseDown: fn(), onDoubleClick: fn() },
    sentMessages: [],
    activeSessionId: "session-001",
  },
  decorators: [
    (Story) => (
      <div className="max-w-2xl mx-auto border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ChatInput>;

export const Default: Story = {};

export const Streaming: Story = {
  args: {
    isStreaming: true,
  },
};

export const WithHistory: Story = {
  args: {
    sentMessages: [
      "Create a new agent for data analysis",
      "Add a web search tool to the agent",
      "Deploy the agent to production",
    ],
  },
};

export const ImagesDisabled: Story = {
  args: {
    imagesAllowed: false,
  },
};

export const TallInput: Story = {
  args: {
    inputHeight: 140,
  },
};
