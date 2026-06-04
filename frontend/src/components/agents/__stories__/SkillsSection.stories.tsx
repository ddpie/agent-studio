import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import SkillsSection from "../SkillsSection";
import { useAgentEditStore } from "../../../stores/agent-edit-store";
import type { AgentSkillEntry } from "../../../lib/agent-metadata";

/**
 * SkillsSection manages the skills attached to an agent. It shows bound
 * skills as cards with status badges (template update available, local changes)
 * and provides add/create/delete actions.
 *
 * Dependencies:
 * - useAgentEditStore (addSkill, removeSkill, setPendingSkillFiles, initSkillFiles)
 * - listSkills (global skill index fetch)
 * - readGlobalSkillFiles / deleteAgentSkill (API calls)
 * - SkillPicker modal (child component)
 *
 * The store is pre-configured via setState in a decorator.
 */

const sampleSkills: AgentSkillEntry[] = [
  {
    id: "sk-001",
    sourceSkillId: "global-sk-web-search",
    sourceContentHash: "abc123",
    name: "web-search",
    description: "Search the web for up-to-date information",
    contentHash: "abc123",
    files: ["SKILL.md", "scripts/search.sh"],
  },
  {
    id: "sk-002",
    sourceSkillId: "global-sk-data-analysis",
    sourceContentHash: "def456",
    name: "data-analysis",
    description: "Analyze datasets and generate visualizations",
    contentHash: "def456",
    files: ["SKILL.md"],
  },
];

const meta: Meta<typeof SkillsSection> = {
  title: "Agents/SkillsSection",
  component: SkillsSection,
  args: {
    skills: sampleSkills,
    agentId: "agt-skills-demo-001",
    onEditSkill: fn(),
  },
  decorators: [
    (Story) => {
      // Set minimal agent-edit store state
      useAgentEditStore.setState({
        agentId: "agt-skills-demo-001",
        formData: { skills: sampleSkills },
      });
      return (
        <div className="max-w-lg mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof SkillsSection>;

/** Default: two skills attached. */
export const Default: Story = {};

/** Empty: no skills yet. Shows placeholder text with add buttons. */
export const Empty: Story = {
  args: {
    skills: [],
  },
};

/** With deployed hashes: shows local-changes indicator for modified skill. */
export const WithLocalChanges: Story = {
  args: {
    skills: sampleSkills,
    deployedHashes: {
      "sk-001": "abc123",
      "sk-002": "old-hash-xyz", // different from contentHash → shows blue dot
    },
  },
};
