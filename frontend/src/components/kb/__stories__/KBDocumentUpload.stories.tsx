import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import KBDocumentUpload from "../KBDocumentUpload";

/**
 * KBDocumentUpload provides a drag-and-drop area for uploading documents
 * to a knowledge base. It handles presigned URL fetching and S3 upload.
 *
 * Dependencies:
 * - getAttachmentUploadUrl / uploadWithPresignedPost (API calls)
 * - useTranslation (i18next) — handled by Storybook preview
 *
 * The upload flow requires a real backend to complete, but the drop zone
 * UI, file validation, and progress list can be explored interactively.
 * The `onUploaded` callback is mocked.
 */

const meta: Meta<typeof KBDocumentUpload> = {
  title: "KB/KBDocumentUpload",
  component: KBDocumentUpload,
  args: {
    kbId: "kb-001",
    onUploaded: fn(async () => {}),
  },
  decorators: [
    (Story) => (
      <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof KBDocumentUpload>;

/** Default idle state: shows the drop zone ready for files. */
export const Default: Story = {};

/** Different KB ID (layout is identical, verifies parameterization). */
export const AnotherKB: Story = {
  args: {
    kbId: "kb-prod-docs",
  },
};
