import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import AgentFormSections from "../AgentFormSections";
import { useWorkspaceStore } from "../../../stores/workspace-store";
import { useAgentEditStore } from "../../../stores/agent-edit-store";
import { useKBStore } from "../../../stores/kb-store";
import type { AgentMetadata } from "../../../lib/agent-metadata";

/**
 * AgentFormSections is the main orchestrating form component for agent
 * editing. It composes multiple sub-sections: BasicInfo, ChatSettings,
 * AgentBehavior, Skills, Tools, MCP, KnowledgeBases, LinkedAgents,
 * Memory, and Secrets.
 *
 * Dependencies:
 * - useEditAssistantStore (AI optimize panel)
 * - useAgentEditStore (skill/tool state)
 * - useWorkspaceStore (role-based permission)
 * - useKBStore (knowledge base list)
 * - Multiple child components (SkillsSection, ToolsEditor, McpTargetSelector, etc.)
 * - API calls via children (secrets, KBs, linked agents)
 *
 * We pre-configure stores so the component renders sections properly.
 * Some children (Secrets, KnowledgeBases) will show loading/error states
 * without a backend, which is acceptable for Storybook.
 */

const sampleFormData: Partial<AgentMetadata> = {
  name: "dataAnalyzer",
  display_name: "Data Analyzer",
  description: "An agent that analyzes CSV data and generates insights",
  default_model_id: "anthropic.claude-sonnet-4-20250514-v1:0",
  system_prompt: "You are a data analysis assistant. Help users analyze their data.",
  tool_definitions: '@tool\ndef analyze_csv(file_path: str) -> str:\n    """Analyze a CSV file."""\n    return "analysis result"',
  tool_names: "analyze_csv",
  welcome_message: "Hello! Upload a CSV file and I will analyze it for you.",
  suggestions: ["Analyze my data", "Generate a chart", "Find anomalies"],
  supports_images: true,
  skills: [],
  mcp_targets: [],
  knowledge_bases: [],
  linked_agents: [],
  memory: { enabled: false, strategies: [] },
  runtime_type: "zip",
};

const meta: Meta<typeof AgentFormSections> = {
  title: "Agents/AgentFormSections",
  component: AgentFormSections,
  args: {
    formData: sampleFormData,
    agentId: "agt-form-demo-001",
    agentName: "dataAnalyzer",
    isCreateMode: false,
    changedFields: {},
    updateField: fn(),
    handleOptimizeField: fn(),
    onEditSkill: fn(),
  },
  decorators: [
    (Story) => {
      useWorkspaceStore.setState({
        currentWorkspace: {
          workspaceId: "ws-storybook",
          name: "Storybook Workspace",
          role: "admin",
        },
        workspaces: [],
        loading: false,
        loaded: true,
      });
      useAgentEditStore.setState({
        agentId: "agt-form-demo-001",
        formData: sampleFormData,
      });
      useKBStore.setState({
        items: [],
        loading: false,
        error: null,
      });
      return (
        <div className="max-w-2xl mx-auto p-4 space-y-4 bg-gray-50 dark:bg-gray-950">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof AgentFormSections>;

/** Default: edit mode with all sections visible for a zip-type agent. */
export const Default: Story = {};

/** Create mode: name field is editable, some sections hidden. */
export const CreateMode: Story = {
  args: {
    isCreateMode: true,
    agentName: null,
    formData: {
      ...sampleFormData,
      name: "",
      display_name: "",
    },
  },
};

/** Harness runtime: Skills, Tools, and MCP sections are hidden. */
export const HarnessRuntime: Story = {
  args: {
    formData: {
      ...sampleFormData,
      runtime_type: "harness",
      default_model_id: "anthropic.claude-sonnet-4-20250514-v1:0",
    },
  },
};

/** With changed fields: shows blue modification indicators. */
export const WithChanges: Story = {
  args: {
    changedFields: {
      description: { old: "Old description", new: "An agent that analyzes CSV data and generates insights" },
      system_prompt: { old: "Old prompt", new: "You are a data analysis assistant." },
    },
  },
};
