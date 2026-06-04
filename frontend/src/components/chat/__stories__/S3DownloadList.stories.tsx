import type { Meta, StoryObj } from "@storybook/react-vite";
import S3DownloadList from "../S3DownloadList";
import type { S3Download } from "../../../stores/chat-store";

const meta: Meta<typeof S3DownloadList> = {
  title: "Chat/S3DownloadList",
  component: S3DownloadList,
  decorators: [
    (Story) => (
      <div className="max-w-xl mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof S3DownloadList>;

const imageDownloads: S3Download[] = [
  { key: "agents/agt-001/outputs/chart-revenue.png", filename: "chart-revenue.png" },
  { key: "agents/agt-001/outputs/heatmap.webp", filename: "heatmap.webp" },
];

const fileDownloads: S3Download[] = [
  { key: "agents/agt-001/outputs/report-q4.pdf", filename: "report-q4.pdf" },
  { key: "agents/agt-001/outputs/data-export.csv", filename: "data-export.csv" },
  { key: "agents/agt-001/outputs/model-weights.bin", filename: "model-weights.bin" },
];

const mixedDownloads: S3Download[] = [...imageDownloads, ...fileDownloads];

export const Default: Story = {
  args: {
    downloads: mixedDownloads,
  },
};

export const ImagesOnly: Story = {
  args: {
    downloads: imageDownloads,
  },
};

export const FilesOnly: Story = {
  args: {
    downloads: fileDownloads,
  },
};

export const Empty: Story = {
  args: {
    downloads: [],
  },
  parameters: {
    docs: {
      description: { story: "Returns null when no downloads are present." },
    },
  },
};
