import type { Meta, StoryObj } from "@storybook/react-vite";
import AgentProposalCard from "../AgentProposalCard";
import { useAgentEditStore } from "../../../stores/agent-edit-store";

/**
 * AgentProposalCard renders a compact card showing a Meta-Agent's proposal
 * for creating a new agent. It parses JSON (with repair for trailing commas)
 * and displays agent name, description, tools, MCP targets, skills, and
 * system prompt in a structured preview.
 *
 * Dependencies:
 * - useAgentEditStore (openNewWithData, addSkill, etc.)
 * - useNavigate (react-router — navigates to edit page)
 * - fetchSkills / readGlobalSkillFiles (API calls for skill attachment)
 *
 * The "Edit & Create" button calls the store + navigate which requires
 * a router context. In Storybook this will error on click but the card
 * renders correctly for visual review.
 *
 * Classification: B (feasible with caveats — renders fine, button click
 * would fail without router but visual testing is the primary goal).
 */

const validProposal = JSON.stringify({
  agent_name: "csvAnalyzer",
  description: "Analyzes CSV files and generates statistical reports with visualizations",
  system_prompt: "You are a professional data analyst. Read CSV data, compute statistics, and produce markdown reports with embedded chart descriptions.",
  tool_definitions: '@tool\ndef analyze_csv(file_path: str, columns: list[str] | None = None) -> str:\n    """Analyze CSV data and return a statistical summary."""\n    import pandas as pd\n    df = pd.read_csv(file_path)\n    return df.describe().to_string()',
  tool_names: "analyze_csv,generate_chart",
  welcome_message: "Hi! Upload a CSV file and I will analyze it for you.",
  suggestions: "Summarize this data|Find correlations|Generate a histogram",
  supports_images: true,
  mcp_targets: ["s3-readonly"],
  skills: ["web-search", "data-analysis"],
});

const minimalProposal = JSON.stringify({
  agent_name: "simpleBot",
  description: "A minimal assistant agent",
  system_prompt: "You are a helpful assistant.",
  tool_definitions: "",
  tool_names: "",
  welcome_message: "Hello!",
  suggestions: "",
  supports_images: false,
});

const meta: Meta<typeof AgentProposalCard> = {
  title: "Chat/AgentProposalCard",
  component: AgentProposalCard,
  decorators: [
    (Story) => {
      // Reset store state so multiple stories don't interfere
      useAgentEditStore.setState({
        agentId: null,
        formData: null,
      });
      return (
        <div className="max-w-xl mx-auto p-4 bg-white dark:bg-gray-900">
          <Story />
        </div>
      );
    },
  ],
};

export default meta;
type Story = StoryObj<typeof AgentProposalCard>;

/** Default: a full proposal with tools, MCP targets, and skills. */
export const Default: Story = {
  args: {
    json: validProposal,
  },
};

/** Minimal proposal: no tools, no MCP, no skills. */
export const Minimal: Story = {
  args: {
    json: minimalProposal,
  },
};

/** Invalid JSON: shows parse error with raw JSON details toggle. */
export const InvalidJson: Story = {
  args: {
    json: '{"agent_name": "broken", "description": missing-quote}',
  },
};

/** Streaming: incomplete JSON shows the pulse loading placeholder. */
export const Streaming: Story = {
  args: {
    json: '{"agent_name": "csvAnalyzer", "description": "Still being generated...',
  },
};
