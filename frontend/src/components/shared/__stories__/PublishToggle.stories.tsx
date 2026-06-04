import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import PublishToggle from "../PublishToggle";

const meta: Meta<typeof PublishToggle> = {
  title: "Shared/PublishToggle",
  component: PublishToggle,
  decorators: [
    (Story) => (
      <div className="flex items-center gap-4 p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof PublishToggle>;

const noopAsync = async () => {};

export const PrivateCanPublish: Story = {
  args: {
    visibility: "private",
    canPublish: true,
    onPublish: fn(noopAsync) as unknown as () => Promise<void>,
    onUnpublish: fn(noopAsync) as unknown as () => Promise<void>,
    onChange: fn(),
    size: "sm",
  },
};

export const PublicCanPublish: Story = {
  args: {
    visibility: "public",
    canPublish: true,
    onPublish: fn(noopAsync) as unknown as () => Promise<void>,
    onUnpublish: fn(noopAsync) as unknown as () => Promise<void>,
    onChange: fn(),
    size: "sm",
  },
};

export const ReadOnlyPublic: Story = {
  args: {
    visibility: "public",
    canPublish: false,
    onPublish: fn(noopAsync) as unknown as () => Promise<void>,
    onUnpublish: fn(noopAsync) as unknown as () => Promise<void>,
  },
};

export const ReadOnlyPrivate: Story = {
  args: {
    visibility: "private",
    canPublish: false,
    onPublish: fn(noopAsync) as unknown as () => Promise<void>,
    onUnpublish: fn(noopAsync) as unknown as () => Promise<void>,
  },
};

export const DisabledWithReason: Story = {
  args: {
    visibility: "private",
    canPublish: true,
    onPublish: fn(noopAsync) as unknown as () => Promise<void>,
    onUnpublish: fn(noopAsync) as unknown as () => Promise<void>,
    disabledReason: "Skill must pass validation before publishing",
    size: "md",
  },
};
