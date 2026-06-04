import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import EndpointDialog from "../EndpointDialog";

const meta: Meta<typeof EndpointDialog> = {
  title: "Agents/EndpointDialog",
  component: EndpointDialog,
  args: {
    onCancel: fn(),
    onSubmit: fn(async () => {}),
    versions: ["3", "2", "1"],
  },
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj<typeof EndpointDialog>;

export const CreateMode: Story = {
  args: {
    mode: "create",
  },
};

export const SwitchMode: Story = {
  args: {
    mode: "switch",
    fixedName: "production",
    versions: ["5", "4", "3", "2", "1"],
  },
};

export const SingleVersion: Story = {
  args: {
    mode: "create",
    versions: ["1"],
  },
};

export const WithError: Story = {
  args: {
    mode: "create",
    versions: ["2", "1"],
    onSubmit: fn(async () => {
      throw new Error("Endpoint name already exists");
    }),
  },
};
