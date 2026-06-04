import type { Meta, StoryObj } from "@storybook/react-vite";
import ToolCallDetails from "../ToolCallDetails";
import type { ToolCallRecord } from "../../../stores/chat-store";

const meta: Meta<typeof ToolCallDetails> = {
  title: "Chat/ToolCallDetails",
  component: ToolCallDetails,
  decorators: [
    (Story) => (
      <div className="max-w-xl mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ToolCallDetails>;

const mockCalls: ToolCallRecord[] = [
  {
    id: "tc-001",
    name: "search_web",
    input: JSON.stringify({ query: "AWS Bedrock AgentCore pricing", max_results: 5 }, null, 2),
    output: JSON.stringify({
      results: [
        { title: "Amazon Bedrock Pricing", url: "https://aws.amazon.com/bedrock/pricing/", snippet: "Pay-as-you-go pricing for foundation models..." },
        { title: "AgentCore Runtime Costs", url: "https://docs.aws.amazon.com/bedrock/agentcore/", snippet: "Runtime charges based on compute time..." },
      ],
    }, null, 2),
    isSvg: false,
  },
  {
    id: "tc-002",
    name: "create_agent",
    input: JSON.stringify({ name: "dataAnalyzer", description: "Analyzes CSV data and produces charts", model_id: "anthropic.claude-sonnet-4-20250514-v1:0" }, null, 2),
    output: JSON.stringify({ agent_id: "agt-abc123", status: "CREATING", message: "Agent creation initiated" }, null, 2),
    isSvg: false,
  },
];

export const Default: Story = {
  args: {
    calls: mockCalls,
  },
};

export const SingleCall: Story = {
  args: {
    calls: [mockCalls[0]],
  },
};

export const WithSvgChart: Story = {
  args: {
    calls: [
      {
        id: "tc-003",
        name: "generate_chart",
        input: JSON.stringify({ type: "bar", data: [10, 25, 15, 30, 20], labels: ["Mon", "Tue", "Wed", "Thu", "Fri"] }),
        output: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
          <rect x="10" y="70" width="20" height="30" fill="#3b82f6"/>
          <rect x="40" y="40" width="20" height="60" fill="#3b82f6"/>
          <rect x="70" y="55" width="20" height="45" fill="#3b82f6"/>
          <rect x="100" y="30" width="20" height="70" fill="#3b82f6"/>
          <rect x="130" y="50" width="20" height="50" fill="#3b82f6"/>
        </svg>`,
        isSvg: true,
      },
    ],
  },
};

export const LongOutput: Story = {
  args: {
    calls: [
      {
        id: "tc-004",
        name: "read_document",
        input: JSON.stringify({ s3_key: "uploads/session-001/report.csv" }),
        output: "id,name,value,category,timestamp\n" +
          Array.from({ length: 50 }, (_, i) =>
            `${i + 1},item_${i + 1},${Math.round(Math.random() * 1000)},cat_${(i % 4) + 1},2026-06-0${(i % 9) + 1}`
          ).join("\n"),
        isSvg: false,
      },
    ],
  },
};

export const Empty: Story = {
  args: {
    calls: [],
  },
};
