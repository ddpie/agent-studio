import type { Meta, StoryObj } from "@storybook/react-vite";
import ImageLightbox from "../ImageLightbox";

const meta: Meta<typeof ImageLightbox> = {
  title: "UI/ImageLightbox",
  component: ImageLightbox,
};

export default meta;
type Story = StoryObj<typeof ImageLightbox>;

const PLACEHOLDER_DATA_URL =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='150' fill='%23e5e7eb'%3E%3Crect width='200' height='150'/%3E%3Ctext x='50%25' y='50%25' dominant-baseline='middle' text-anchor='middle' fill='%236b7280' font-size='14'%3EPreview%3C/text%3E%3C/svg%3E";

export const DataUrl: Story = {
  args: { src: PLACEHOLDER_DATA_URL, alt: "Sample image" },
};

export const FailedLoad: Story = {
  args: { src: "https://invalid.example.com/missing.png", alt: "Missing image" },
};
