import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import KBDocumentTable from "../KBDocumentTable";

const meta: Meta<typeof KBDocumentTable> = {
  title: "KB/KBDocumentTable",
  component: KBDocumentTable,
  args: {
    onDelete: fn().mockResolvedValue(undefined),
  },
};

export default meta;
type Story = StoryObj<typeof KBDocumentTable>;

export const Default: Story = {
  args: {
    documents: [
      {
        key: "kb/docs/product-spec.pdf",
        filename: "product-spec.pdf",
        sizeBytes: 2_450_000,
        lastModified: "2026-05-28T14:30:00Z",
      },
      {
        key: "kb/docs/api-reference.md",
        filename: "api-reference.md",
        sizeBytes: 87_400,
        lastModified: "2026-06-01T09:15:00Z",
      },
      {
        key: "kb/docs/onboarding-guide.docx",
        filename: "onboarding-guide.docx",
        sizeBytes: 524_288,
        lastModified: "2026-05-20T16:45:00Z",
      },
    ],
  },
};

export const Empty: Story = {
  args: { documents: [] },
};

export const SingleDocument: Story = {
  args: {
    documents: [
      {
        key: "kb/docs/faq.txt",
        filename: "faq.txt",
        sizeBytes: 4_200,
        lastModified: "2026-06-03T11:00:00Z",
      },
    ],
  },
};

export const LargeFiles: Story = {
  args: {
    documents: [
      {
        key: "kb/docs/training-data.jsonl",
        filename: "training-data.jsonl",
        sizeBytes: 1_073_741_824,
        lastModified: "2026-05-15T08:00:00Z",
      },
      {
        key: "kb/docs/embeddings-cache.bin",
        filename: "embeddings-cache.bin",
        sizeBytes: 536_870_912,
        lastModified: "2026-05-16T10:30:00Z",
      },
    ],
  },
};
