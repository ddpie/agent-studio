import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import ToolPicker from "../ToolPicker";
import { useToolLibraryStore } from "../../../stores/tool-library-store";

const mockTools = [
  {
    id: "search_web",
    name: "search_web",
    description: "Search the web using Brave Search API and return structured results",
    category: "search",
    code: '@tool\ndef search_web(query: str, max_results: int = 5) -> str:\n    """Search the web."""\n    pass',
    builtin: true,
    owner: "system",
    visibility: "shared",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
  },
  {
    id: "read_s3_file",
    name: "read_s3_file",
    description: "Read a file from S3 bucket and return its contents as text",
    category: "storage",
    code: '@tool\ndef read_s3_file(s3_key: str) -> str:\n    """Read file from S3."""\n    pass',
    builtin: true,
    owner: "system",
    visibility: "shared",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
  },
  {
    id: "generate_chart",
    name: "generate_chart",
    description: "Generate SVG charts (bar, line, pie) from data arrays",
    category: "visualization",
    code: '@tool\ndef generate_chart(chart_type: str, data: list) -> str:\n    """Generate chart."""\n    pass',
    builtin: true,
    owner: "system",
    visibility: "shared",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-15T00:00:00Z",
  },
  {
    id: "execute_sql",
    name: "execute_sql",
    description: "Execute SQL queries against connected database and return results",
    category: "database",
    code: '@tool\ndef execute_sql(query: str, database: str = "default") -> str:\n    """Execute SQL."""\n    pass',
    builtin: false,
    owner: "user-123",
    visibility: "private",
    created_at: "2026-05-20T00:00:00Z",
    updated_at: "2026-05-25T00:00:00Z",
  },
  {
    id: "send_email",
    name: "send_email",
    description: "Send an email to the specified recipient with subject and body",
    category: "communication",
    code: '@tool\ndef send_email(to: str, subject: str, body: str) -> str:\n    """Send email."""\n    pass',
    builtin: false,
    owner: "user-123",
    visibility: "private",
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
  },
];

const meta: Meta<typeof ToolPicker> = {
  title: "Agents/ToolPicker",
  component: ToolPicker,
  args: {
    open: true,
    onClose: fn(),
    onSelect: fn(),
    existingToolNames: [],
  },
  parameters: {
    layout: "fullscreen",
  },
  decorators: [
    (Story) => {
      // Pre-populate the store with mock tools
      useToolLibraryStore.setState({
        tools: mockTools,
        loading: false,
        error: null,
      });
      return <Story />;
    },
  ],
};

export default meta;
type Story = StoryObj<typeof ToolPicker>;

export const Default: Story = {};

export const WithExistingTools: Story = {
  args: {
    existingToolNames: ["search_web", "read_s3_file"],
  },
};

export const Loading: Story = {
  decorators: [
    (Story) => {
      useToolLibraryStore.setState({ tools: [], loading: true, error: null });
      return <Story />;
    },
  ],
};

export const Empty: Story = {
  decorators: [
    (Story) => {
      useToolLibraryStore.setState({ tools: [], loading: false, error: null });
      return <Story />;
    },
  ],
};

export const Closed: Story = {
  args: {
    open: false,
  },
};
