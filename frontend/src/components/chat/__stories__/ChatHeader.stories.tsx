import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import ChatHeader from "../ChatHeader";
import type { ChatSession } from "../../../stores/chat-store";

/**
 * ChatHeader displays the agent name, model selector, session history,
 * export button, and new-session button.
 *
 * Dependencies:
 * - useMetaAgentStatus (API call) — will return null in Storybook (no backend)
 * - useKiroModels (API call) — same, returns [] so the static MODEL_GROUPS fallback works
 * - IntegrationTab — rendered in a modal, isolated
 */

const mockSessions: ChatSession[] = [
  {
    id: "sess-001",
    agentKey: "meta",
    title: "Create data analyzer agent",
    messages: [
      { id: "m1", role: "user", content: "Create an agent", timestamp: Date.now() - 3600000 },
      { id: "m2", role: "assistant", content: "Done!", timestamp: Date.now() - 3500000 },
    ],
    createdAt: Date.now() - 7200000,
    updatedAt: Date.now() - 3500000,
  },
  {
    id: "sess-002",
    agentKey: "meta",
    title: "Debug deployment issue",
    messages: [
      { id: "m3", role: "user", content: "Why is the agent failing?", timestamp: Date.now() - 86400000 },
      { id: "m4", role: "assistant", content: "Checking logs...", timestamp: Date.now() - 86300000 },
      { id: "m5", role: "assistant", content: "Found the issue.", timestamp: Date.now() - 86200000 },
    ],
    createdAt: Date.now() - 86400000,
    updatedAt: Date.now() - 86200000,
  },
];

const meta: Meta<typeof ChatHeader> = {
  title: "Chat/ChatHeader",
  component: ChatHeader,
  args: {
    agentId: undefined,
    agentName: null,
    selectedModel: "anthropic.claude-sonnet-4-20250514-v1:0",
    onModelChange: fn(),
    sessions: mockSessions,
    activeSessionId: "sess-001",
    onLoadSession: fn(),
    onDeleteSession: fn(),
    onNewSession: fn(),
    messages: [
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi there!" },
    ],
  },
  decorators: [
    (Story) => (
      <div className="w-full max-w-4xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ChatHeader>;

/** Meta-Agent header (no agentId) shows status dot and A2A button. */
export const MetaAgent: Story = {};

/** Agent-specific header shows agent name and model picker. */
export const AgentChat: Story = {
  args: {
    agentId: "agt-abc123",
    agentName: "DataAnalyzer",
    selectedModel: "anthropic.claude-sonnet-4-20250514-v1:0",
  },
};

/** No sessions yet — history dropdown shows empty state. */
export const EmptyHistory: Story = {
  args: {
    sessions: [],
    activeSessionId: null,
  },
};

/** No messages — export button is hidden. */
export const NoMessages: Story = {
  args: {
    messages: [],
  },
};
