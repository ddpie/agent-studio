import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import MessageList from "../MessageList";
import type { Message } from "../../../stores/chat-store";

const meta: Meta<typeof MessageList> = {
  title: "Chat/MessageList",
  component: MessageList,
  decorators: [
    (Story) => (
      <div className="h-[500px] flex flex-col bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg overflow-hidden">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof MessageList>;

const now = Date.now();

const sampleMessages: Message[] = [
  {
    id: "msg-001",
    role: "user",
    content: "Create a new agent that analyzes customer support tickets and categorizes them by urgency.",
    timestamp: now - 60000,
  },
  {
    id: "msg-002",
    role: "assistant",
    content: "I'll create a support ticket analyzer agent for you. Let me set that up with the appropriate tools and system prompt.\n\nThe agent will use NLP to classify tickets into **Critical**, **High**, **Medium**, and **Low** urgency categories based on keywords, sentiment, and customer tier.",
    timestamp: now - 55000,
  },
  {
    id: "msg-003",
    role: "user",
    content: "Can you also add a tool that fetches tickets from our Zendesk instance?",
    timestamp: now - 30000,
  },
  {
    id: "msg-004",
    role: "assistant",
    content: "Done! I've added a `fetch_zendesk_tickets` tool that connects to your Zendesk API. It supports filtering by status, priority, and date range. You'll need to configure your Zendesk API key in the agent's secrets.",
    timestamp: now - 25000,
  },
];

export const Default: Story = {
  args: {
    messages: sampleMessages,
    isStreaming: false,
    statusText: null,
    activeTool: null,
    autoContinue: null,
    onRegenerate: fn(),
    emptyState: <div className="text-center text-gray-400 py-12">Ask me anything about your agents</div>,
  },
};

export const Empty: Story = {
  args: {
    messages: [],
    isStreaming: false,
    statusText: null,
    activeTool: null,
    autoContinue: null,
    onRegenerate: fn(),
    emptyState: (
      <div className="text-center text-gray-400 py-12">
        <p className="text-lg font-medium mb-2">Welcome to Agent Studio</p>
        <p className="text-sm">Describe the agent you want to build and I'll help you create it.</p>
      </div>
    ),
  },
};

export const Streaming: Story = {
  args: {
    messages: [
      ...sampleMessages.slice(0, 3),
      {
        id: "msg-streaming",
        role: "assistant" as const,
        content: "Let me look into the Zendesk integration. I'll need to configure the API endpoint and authentication...",
        timestamp: now - 5000,
      },
    ],
    isStreaming: true,
    statusText: null,
    activeTool: null,
    autoContinue: null,
    onRegenerate: fn(),
    emptyState: null,
  },
};

export const WithActiveTool: Story = {
  args: {
    messages: sampleMessages.slice(0, 2),
    isStreaming: true,
    statusText: null,
    activeTool: "create_agent",
    autoContinue: null,
    onRegenerate: fn(),
    emptyState: null,
  },
};

export const WithStatusText: Story = {
  args: {
    messages: sampleMessages.slice(0, 2),
    isStreaming: true,
    statusText: "Deploying agent to AgentCore Runtime...",
    activeTool: null,
    autoContinue: null,
    onRegenerate: fn(),
    emptyState: null,
  },
};

export const WithAutoContinue: Story = {
  args: {
    messages: sampleMessages,
    isStreaming: true,
    statusText: null,
    activeTool: null,
    autoContinue: { n: 2, max: 5 },
    onRegenerate: fn(),
    emptyState: null,
  },
};
