import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import KBIngestionStatus from "../KBIngestionStatus";

const meta: Meta<typeof KBIngestionStatus> = {
  title: "KB/KBIngestionStatus",
  component: KBIngestionStatus,
  args: {
    onRefresh: fn().mockResolvedValue(undefined),
  },
};

export default meta;
type Story = StoryObj<typeof KBIngestionStatus>;

export const Default: Story = {
  args: {
    ingestion: null,
  },
};

export const InProgress: Story = {
  args: {
    ingestion: {
      status: "IN_PROGRESS",
      documentsScanned: 12,
      documentsIndexed: 5,
      documentsFailed: 0,
    },
  },
};

export const Complete: Story = {
  args: {
    ingestion: {
      status: "COMPLETE",
      documentsScanned: 25,
      documentsIndexed: 25,
      documentsFailed: 0,
      processedSuccessfully: 25,
    },
  },
};

export const PartialSuccess: Story = {
  args: {
    ingestion: {
      status: "COMPLETE",
      documentsScanned: 20,
      documentsIndexed: 17,
      documentsFailed: 3,
      processedSuccessfully: 17,
    },
  },
};

export const Failed: Story = {
  args: {
    ingestion: {
      status: "FAILED",
      documentsScanned: 10,
      documentsIndexed: 0,
      documentsFailed: 10,
      failureReasons: [
        "Unsupported file format: .pptx",
        "Document exceeds maximum size limit (50MB)",
      ],
    },
  },
};
