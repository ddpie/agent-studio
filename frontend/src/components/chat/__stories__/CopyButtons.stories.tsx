import type { Meta, StoryObj } from "@storybook/react-vite";
import CopyButtons from "../CopyButtons";

const meta: Meta<typeof CopyButtons> = {
  title: "Chat/CopyButtons",
  component: CopyButtons,
  decorators: [
    (Story) => (
      <div className="relative group max-w-md mx-auto p-8 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg">
        <p className="text-sm text-gray-700 dark:text-gray-300 mb-4">
          Hover over this area to see the copy buttons appear in the top-right corner.
        </p>
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof CopyButtons>;

export const Default: Story = {
  args: {
    content: "Here is the analysis of your data:\n\n## Key Findings\n\n1. **Revenue** increased by 23% quarter-over-quarter\n2. Customer retention rate improved to *94.5%*\n3. Top-performing region: `us-east-1`\n\n```python\ndf.groupby('region').agg({'revenue': 'sum'}).sort_values('revenue', ascending=False)\n```",
  },
};

export const PlainText: Story = {
  args: {
    content: "The agent has been successfully deployed. You can now invoke it using the endpoint URL.",
  },
};

export const WithCodeBlock: Story = {
  args: {
    content: "```python\n@tool\ndef search_web(query: str, max_results: int = 5) -> str:\n    \"\"\"Search the web for information.\"\"\"\n    results = brave_search(query, count=max_results)\n    return json.dumps(results)\n```\n\nThis tool uses the Brave Search API to fetch results.",
  },
};

export const LongContent: Story = {
  args: {
    content: Array.from({ length: 20 }, (_, i) =>
      `- Item ${i + 1}: Processing batch ${i + 1} with ${Math.round(Math.random() * 100)} records`
    ).join("\n"),
  },
};
