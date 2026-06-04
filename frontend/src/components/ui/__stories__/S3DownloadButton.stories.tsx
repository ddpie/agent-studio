import type { Meta, StoryObj } from "@storybook/react-vite";
import S3DownloadButton from "../S3DownloadButton";

const meta: Meta<typeof S3DownloadButton> = {
  title: "UI/S3DownloadButton",
  component: S3DownloadButton,
  args: {
    s3Key: "agents/agent-001/outputs/analysis-report.csv",
    filename: "analysis-report.csv",
    size: "sm",
  },
};

export default meta;
type Story = StoryObj<typeof S3DownloadButton>;

export const Default: Story = {};

export const MediumSize: Story = {
  args: {
    s3Key: "agents/agent-002/outputs/training-results.jsonl",
    filename: "training-results.jsonl",
    size: "md",
  },
};

export const LongFilename: Story = {
  args: {
    s3Key: "agents/agent-003/outputs/2026-06-04-quarterly-performance-metrics-dashboard.xlsx",
    filename: "2026-06-04-quarterly-performance-metrics-dashboard.xlsx",
  },
};

export const ImageFile: Story = {
  args: {
    s3Key: "agents/agent-004/outputs/chart-visualization.png",
    filename: "chart-visualization.png",
    size: "md",
  },
};
