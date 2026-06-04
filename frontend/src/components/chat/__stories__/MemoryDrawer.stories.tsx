import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { useEffect } from "react";
import MemoryDrawer from "../MemoryDrawer";
import { useMemoryStore } from "../../../stores/memory-store";

/**
 * MemoryDrawer shows agent memory records (preferences, facts, summaries, episodes)
 * in a slide-over panel. We pre-populate the Zustand store in a decorator.
 */

function WithMockMemory({
  children,
  records,
  loading,
}: {
  children: React.ReactNode;
  records?: boolean;
  loading?: boolean;
}) {
  useEffect(() => {
    if (loading) {
      useMemoryStore.setState({
        byAgent: {
          "agent-001": {
            preferences: { records: [], nextToken: null, loadingMore: false },
            facts: { records: [], nextToken: null, loadingMore: false },
            summaries: { records: [], nextToken: null, loadingMore: false },
            episodes: { records: [], nextToken: null, loadingMore: false },
            loading: true,
          },
        },
      });
    } else if (records) {
      useMemoryStore.setState({
        byAgent: {
          "agent-001": {
            preferences: {
              records: [
                {
                  id: "rec-pref-1",
                  namespace: "preferences",
                  content: { preference: "Prefers concise responses", context: "User asked for brevity", categories: ["style", "communication"] },
                  createdAt: new Date(Date.now() - 3600000).toISOString() as unknown as number,
                },
                {
                  id: "rec-pref-2",
                  namespace: "preferences",
                  content: { preference: "Uses Python for data analysis", context: "Mentioned in multiple sessions", categories: ["technical"] },
                  createdAt: new Date(Date.now() - 86400000).toISOString() as unknown as number,
                },
              ],
              nextToken: null,
              loadingMore: false,
            },
            facts: {
              records: [
                {
                  id: "rec-fact-1",
                  namespace: "facts",
                  content: { text: "User's project is called Agent Studio" },
                  createdAt: new Date(Date.now() - 172800000).toISOString() as unknown as number,
                },
              ],
              nextToken: "cursor-123",
              loadingMore: false,
            },
            summaries: {
              records: [
                {
                  id: "rec-sum-1",
                  namespace: "summaries",
                  content: { text: "Discussed deployment pipeline, decided to use CDK with Lambda for infrastructure management." },
                  createdAt: new Date(Date.now() - 259200000).toISOString() as unknown as number,
                },
              ],
              nextToken: null,
              loadingMore: false,
            },
            episodes: { records: [], nextToken: null, loadingMore: false },
            loading: false,
          },
        },
      });
    } else {
      useMemoryStore.setState({
        byAgent: {
          "agent-001": {
            preferences: { records: [], nextToken: null, loadingMore: false },
            facts: { records: [], nextToken: null, loadingMore: false },
            summaries: { records: [], nextToken: null, loadingMore: false },
            episodes: { records: [], nextToken: null, loadingMore: false },
            loading: false,
          },
        },
      });
    }
  }, [records, loading]);

  return <>{children}</>;
}

const meta: Meta<typeof MemoryDrawer> = {
  title: "Chat/MemoryDrawer",
  component: MemoryDrawer,
  args: {
    open: true,
    onClose: fn(),
    workspaceId: "ws-001",
    agentId: "agent-001",
  },
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj<typeof MemoryDrawer>;

export const WithRecords: Story = {
  decorators: [
    (Story) => (
      <WithMockMemory records>
        <Story />
      </WithMockMemory>
    ),
  ],
};

export const Empty: Story = {
  decorators: [
    (Story) => (
      <WithMockMemory records={false}>
        <Story />
      </WithMockMemory>
    ),
  ],
};

export const Loading: Story = {
  decorators: [
    (Story) => (
      <WithMockMemory loading>
        <Story />
      </WithMockMemory>
    ),
  ],
};

export const Closed: Story = {
  args: {
    open: false,
  },
};
