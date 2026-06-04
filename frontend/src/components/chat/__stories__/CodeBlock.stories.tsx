import type { Meta, StoryObj } from "@storybook/react-vite";
import CodeBlock from "../CodeBlock";

const meta: Meta<typeof CodeBlock> = {
  title: "Chat/CodeBlock",
  component: CodeBlock,
};

export default meta;
type Story = StoryObj<typeof CodeBlock>;

export const Python: Story = {
  args: {
    language: "python",
    code: `from strands import tool

@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for information."""
    results = brave_search(query, count=max_results)
    return json.dumps(results, indent=2)`,
  },
};

export const TypeScript: Story = {
  args: {
    language: "typescript",
    code: `interface AgentConfig {
  name: string;
  modelId: string;
  systemPrompt: string;
  tools: string[];
}

export function createAgent(config: AgentConfig): Promise<Agent> {
  return apiClient.post('/agents', config);
}`,
  },
};

export const JSON: Story = {
  args: {
    language: "json",
    code: `{
  "agentId": "abc123",
  "status": "READY",
  "runtime": {
    "modelId": "anthropic.claude-sonnet-4-6",
    "memoryEnabled": true
  }
}`,
  },
};

export const Bash: Story = {
  args: {
    language: "bash",
    code: `#!/bin/bash
cd infra && npx cdk deploy --all
bash scripts/deploy-agentcore.sh
bash scripts/deploy-all.sh --only-frontend`,
  },
};
