import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import SkillFileDialogs from "../SkillFileDialogs";

const meta: Meta<typeof SkillFileDialogs> = {
  title: "Pages/SkillFileDialogs",
  component: SkillFileDialogs,
  args: {
    availableDirs: ["(root)", "scripts", "tests", "utils"],
    onClose: fn(),
    onConfirm: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof SkillFileDialogs>;

export const NewFile: Story = {
  args: {
    activeDialog: {
      type: "newFile",
      context: { parentDir: "scripts" },
    },
  },
};

export const NewFolder: Story = {
  args: {
    activeDialog: {
      type: "newFolder",
      context: {},
    },
  },
};

export const Rename: Story = {
  args: {
    activeDialog: {
      type: "rename",
      context: { path: "scripts/run.sh", currentName: "run.sh" },
    },
  },
};

export const MoveFile: Story = {
  args: {
    activeDialog: {
      type: "move",
      context: { path: "scripts/helper.py" },
    },
  },
};

export const DeleteFile: Story = {
  args: {
    activeDialog: {
      type: "deleteFile",
      context: { path: "tests/old-test.py" },
    },
  },
};

export const Closed: Story = {
  args: {
    activeDialog: null,
  },
};
