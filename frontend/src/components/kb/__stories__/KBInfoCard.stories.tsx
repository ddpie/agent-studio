import type { Meta, StoryObj } from "@storybook/react-vite";
import KBInfoCard from "../KBInfoCard";
import type { KBDetailResponse } from "../../../lib/api-client";

const baseKB: KBDetailResponse = {
  kbId: "kb-a1b2c3d4",
  name: "Product Documentation",
  description: "Internal product docs including API reference, architecture guides, and onboarding materials.",
  status: "ACTIVE",
  docCount: 47,
  updatedAt: "2026-06-02T18:00:00Z",
  bedrockKbId: "ABCDEFGH12",
  embeddingModel: "amazon.titan-embed-text-v2:0",
  documents: [],
  ingestion: null,
  attachedAgentIds: ["agent-001", "agent-002"],
  createdAt: "2026-05-10T09:30:00Z",
  createdBy: "user@example.com",
};

const meta: Meta<typeof KBInfoCard> = {
  title: "KB/KBInfoCard",
  component: KBInfoCard,
  args: {
    kb: baseKB,
  },
};

export default meta;
type Story = StoryObj<typeof KBInfoCard>;

export const Default: Story = {};

export const Creating: Story = {
  args: {
    kb: { ...baseKB, status: "CREATING", docCount: 0 },
  },
};

export const Failed: Story = {
  args: {
    kb: { ...baseKB, status: "FAILED", description: "" },
  },
};

export const NoDescription: Story = {
  args: {
    kb: { ...baseKB, description: "", embeddingModel: "" },
  },
};
