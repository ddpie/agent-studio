import type { Meta, StoryObj } from "@storybook/react-vite";
import ChatMessage from "../ChatMessage";
import type { Message, ToolCallRecord, MessageBlock } from "../../../stores/chat-store";

/**
 * ChatMessage renders a single message bubble (user or assistant).
 *
 * Store deps (resolved without providers since Zustand stores are global):
 * - useUISettings: reads `showInlineToolCalls`
 * - useChatStore: reads `editAndResend`
 */

const meta: Meta<typeof ChatMessage> = {
  title: "Chat/ChatMessage",
  component: ChatMessage,
  decorators: [
    (Story) => (
      <div className="max-w-2xl mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
  parameters: {
    layout: "centered",
  },
};

export default meta;
type Story = StoryObj<typeof ChatMessage>;

const baseUserMessage: Message = {
  id: "msg-user-001",
  role: "user",
  content: "Create an agent that can analyze CSV data and generate charts",
  timestamp: Date.now(),
};

const baseAssistantMessage: Message = {
  id: "msg-asst-001",
  role: "assistant",
  content:
    "I'll create a data analysis agent for you. This agent will be able to:\n\n1. **Read CSV files** from uploads\n2. **Analyze data** using pandas-like operations\n3. **Generate charts** as SVG visualizations\n\nLet me set this up now.",
  timestamp: Date.now(),
};

export const UserMessage: Story = {
  args: {
    message: baseUserMessage,
    isLastAssistant: false,
    isStreaming: false,
  },
};

export const AssistantMessage: Story = {
  args: {
    message: baseAssistantMessage,
    isLastAssistant: true,
    isStreaming: false,
  },
};

export const StreamingAssistant: Story = {
  args: {
    message: {
      ...baseAssistantMessage,
      content: "I'm working on creating your agent. Let me first check the available tools",
    },
    isLastAssistant: true,
    isStreaming: true,
  },
};

export const EmptyAssistant: Story = {
  args: {
    message: {
      id: "msg-asst-empty",
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    },
    isLastAssistant: true,
    isStreaming: true,
  },
};

const toolCalls: ToolCallRecord[] = [
  {
    id: "tc-001",
    name: "create_agent",
    input: JSON.stringify(
      { name: "dataAnalyzer", description: "Analyzes CSV data", model_id: "anthropic.claude-sonnet-4-20250514-v1:0" },
      null,
      2,
    ),
    output: JSON.stringify({ agent_id: "agt-xyz", status: "CREATING" }, null, 2),
    isSvg: false,
  },
];

export const WithToolCalls: Story = {
  args: {
    message: {
      ...baseAssistantMessage,
      toolCalls,
    },
    isLastAssistant: true,
    isStreaming: false,
  },
};

const blocks: MessageBlock[] = [
  { kind: "text", text: "Let me create that agent for you." },
  {
    kind: "tool_call",
    call: toolCalls[0],
  },
  { kind: "text", text: "Done! Your agent is being provisioned." },
];

export const WithInlineBlocks: Story = {
  args: {
    message: {
      ...baseAssistantMessage,
      content: "Let me create that agent for you.Done! Your agent is being provisioned.",
      toolCalls,
      blocks,
    },
    isLastAssistant: true,
    isStreaming: false,
  },
};

export const WithImages: Story = {
  args: {
    message: {
      ...baseUserMessage,
      content: "Here is the screenshot of the error",
      images: [
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='100'%3E%3Crect fill='%23e2e8f0' width='200' height='100'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' dy='.3em' fill='%2364748b'%3EImage%3C/text%3E%3C/svg%3E",
      ],
    },
    isLastAssistant: false,
    isStreaming: false,
  },
};

export const WithAttachments: Story = {
  args: {
    message: {
      ...baseUserMessage,
      content: "Please analyze this file",
      attachments: [
        { name: "sales-data.csv", size: 45200, s3Key: "uploads/session-001/sales-data.csv" },
        { name: "config.json", size: 1024, s3Key: "uploads/session-001/config.json" },
      ],
    },
    isLastAssistant: false,
    isStreaming: false,
  },
};

export const WithErrorBlock: Story = {
  args: {
    message: {
      id: "msg-asst-err",
      role: "assistant",
      content: "I was processing your request when",
      blocks: [
        { kind: "text", text: "I was processing your request when" },
        { kind: "error", text: "Connection lost — streaming was interrupted. Please retry." },
      ],
      timestamp: Date.now(),
    },
    isLastAssistant: true,
    isStreaming: false,
  },
};
