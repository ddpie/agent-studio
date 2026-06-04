import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import KnowledgeBaseSection from "../KnowledgeBaseSection";
import { useWorkspaceStore } from "../../../stores/workspace-store";
import { useKBStore } from "../../../stores/kb-store";

/**
 * KnowledgeBaseSection lets users attach/detach Bedrock knowledge bases
 * for RAG retrieval. It reads from the kb-store for available KBs and
 * uses workspace role for permission gating.
 *
 * Dependencies:
 * - useKBStore (items list, fetchList)
 * - useWorkspaceStore (role-based permission)
 * - attachKnowledgeBase / detachKnowledgeBase (API calls)
 * - shared/Section (presentational wrapper)
 *
 * We pre-populate the kb-store with mock data so the component renders
 * without a backend.
 */

const mockKBs = [
  { kbId: "kb-001", name: "Product Documentation", description: "All product docs", status: "ACTIVE", docCount: 42, updatedAt: "2026-05-30T10:00:00Z" },
  { kbId: "kb-002", name: "Customer FAQ", description: "Frequently asked questions", status: "ACTIVE", docCount: 15, updatedAt: "2026-05-28T08:00:00Z" },
  { kbId: "kb-003", name: "Internal Wiki", description: "Engineering wiki", status: "CREATING", docCount: 0, updatedAt: "2026-06-01T14:00:00Z" },
];

const meta: Meta<typeof KnowledgeBaseSection> = {
  title: "Agents/KnowledgeBaseSection",
  component: KnowledgeBaseSection,
  args: {
    agentId: "agt-kb-demo-001",
    knowledgeBases: ["kb-001"],
    onChange: fn(),
  },
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "admin",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      useKBStore.setState({
        items: mockKBs,
        loading: false,
        error: null,
      });
      return (
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof KnowledgeBaseSection>;

/** Default: one KB attached, two available in the dropdown. */
export const Default: Story = {};

/** Empty: no KBs attached yet. */
export const NoAttachedKBs: Story = {
  args: {
    knowledgeBases: [],
  },
};

/** Multiple attached: shows the list with status badges and doc counts. */
export const MultipleAttached: Story = {
  args: {
    knowledgeBases: ["kb-001", "kb-002", "kb-003"],
  },
};

/** Viewer role: attach/detach buttons are disabled. */
export const ViewerRole: Story = {
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "viewer",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      useKBStore.setState({
        items: mockKBs,
        loading: false,
        error: null,
      });
      return (
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
  args: {
    knowledgeBases: ["kb-001"],
  },
};
