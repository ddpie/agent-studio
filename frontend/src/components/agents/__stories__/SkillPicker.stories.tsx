import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import SkillPicker from "../SkillPicker";

// Mock the listSkills API call
const mockSkills = [
  { id: "skill-001", name: "web-search", description: "Search the web for up-to-date information using Brave Search API", type: "prompt", visibility: "shared" },
  { id: "skill-002", name: "data-analysis", description: "Analyze CSV/JSON datasets, compute statistics, and generate visualizations", type: "prompt", visibility: "shared" },
  { id: "skill-003", name: "code-review", description: "Review code for bugs, security issues, and suggest improvements", type: "prompt", visibility: "shared" },
  { id: "skill-004", name: "document-writer", description: "Generate structured documents, reports, and presentations from raw data", type: "prompt", visibility: "private" },
  { id: "skill-005", name: "sql-generator", description: "Convert natural language queries into optimized SQL statements", type: "prompt", visibility: "shared" },
  { id: "skill-006", name: "api-tester", description: "Test REST APIs with parameterized requests and validate responses", type: "prompt", visibility: "private" },
];

// We need to mock the listSkills module. Since SkillPicker calls listSkills() internally,
// we use module-level mocking via storybook loaders or decorators approach.
// For simplicity, we'll use the component with a beforeEach that patches the module.
const meta: Meta<typeof SkillPicker> = {
  title: "Agents/SkillPicker",
  component: SkillPicker,
  args: {
    open: true,
    onClose: fn(),
    onSelect: fn(),
    existingSkillSourceIds: [],
  },
  parameters: {
    layout: "fullscreen",
  },
  beforeEach: async () => {
    // Mock the skill-storage module's listSkills function
    const mod = await import("../../../lib/skill-storage");
    const original = mod.listSkills;
    (mod as any).listSkills = () => Promise.resolve(mockSkills);
    return () => { (mod as any).listSkills = original; };
  },
};

export default meta;
type Story = StoryObj<typeof SkillPicker>;

export const Default: Story = {};

export const WithExistingSkills: Story = {
  args: {
    existingSkillSourceIds: ["skill-001", "skill-003"],
  },
};

export const AllSkillsAdded: Story = {
  args: {
    existingSkillSourceIds: mockSkills.map((s) => s.id),
  },
};

export const Closed: Story = {
  args: {
    open: false,
  },
};
