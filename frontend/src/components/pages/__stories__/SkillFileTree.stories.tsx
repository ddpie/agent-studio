import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import SkillFileTree from "../SkillFileTree";
import type { TreeNode } from "../../../lib/tree-helpers";

const sampleTreeData: TreeNode[] = [
  { id: "SKILL.md", name: "SKILL.md" },
  {
    id: "__dir__scripts",
    name: "scripts",
    children: [
      { id: "scripts/run.sh", name: "run.sh" },
      { id: "scripts/setup.py", name: "setup.py" },
      { id: "scripts/validate.py", name: "validate.py" },
    ],
  },
  {
    id: "__dir__tests",
    name: "tests",
    children: [
      { id: "tests/test_main.py", name: "test_main.py" },
    ],
  },
  { id: "config.json", name: "config.json" },
  { id: "README.md", name: "README.md" },
];

const meta: Meta<typeof SkillFileTree> = {
  title: "Pages/SkillFileTree",
  component: SkillFileTree,
  decorators: [
    (Story) => (
      <div style={{ display: "flex", height: 500, border: "1px solid #e5e7eb" }}>
        <Story />
      </div>
    ),
  ],
  args: {
    treeData: sampleTreeData,
    currentFile: "SKILL.md",
    changedFiles: new Set<string>(),
    pendingDeletes: new Set<string>(),
    pendingDeleteDirs: new Set<string>(),
    pendingCreates: new Map<string, string>(),
    sidebarWidth: 220,
    dragHandleProps: { onMouseDown: fn(), onDoubleClick: fn() },
    onSelectFile: fn(),
    onNewFile: fn(),
    onNewFolder: fn(),
    onRename: fn(),
    onMove: fn(),
    onDeleteFile: fn(),
    onStageMove: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof SkillFileTree>;

export const Default: Story = {};

export const WithChanges: Story = {
  args: {
    currentFile: "scripts/run.sh",
    changedFiles: new Set(["scripts/run.sh", "config.json"]),
  },
};

export const WithPendingCreates: Story = {
  args: {
    pendingCreates: new Map([
      ["scripts/new-tool.py", "# new tool\n"],
      ["tests/test_new.py", "# test\n"],
    ]),
  },
};

export const WithPendingDeletes: Story = {
  args: {
    pendingDeletes: new Set(["README.md", "tests/test_main.py"]),
  },
};

export const NarrowSidebar: Story = {
  args: {
    sidebarWidth: 160,
  },
};
