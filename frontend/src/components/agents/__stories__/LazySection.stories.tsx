import type { Meta, StoryObj } from "@storybook/react-vite";
import LazySection from "../LazySection";

const meta: Meta<typeof LazySection> = {
  title: "Agents/LazySection",
  component: LazySection,
  decorators: [
    (Story) => (
      <div className="max-w-2xl mx-auto p-4 bg-white dark:bg-gray-900">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof LazySection>;

export const Default: Story = {
  args: {
    id: "configuration",
    children: (
      <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Agent Configuration</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          Configure the agent name, description, system prompt, and model settings.
          This section loads lazily when scrolled into view.
        </p>
      </div>
    ),
  },
};

export const Eager: Story = {
  args: {
    id: "tools",
    eager: true,
    children: (
      <div className="p-4 border border-blue-200 dark:border-blue-800 rounded-lg bg-blue-50 dark:bg-blue-950">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Tools (Eager)</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          This section renders immediately because eager=true. Typically used for the
          initially active/visible section in the agent editor.
        </p>
        <ul className="mt-2 space-y-1 text-xs text-gray-500 dark:text-gray-400">
          <li>search_web - Search the internet</li>
          <li>read_s3_file - Read files from storage</li>
          <li>generate_chart - Create visualizations</li>
        </ul>
      </div>
    ),
  },
};

export const CustomMinHeight: Story = {
  args: {
    id: "skills",
    minHeight: 400,
    children: (
      <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Skills</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          This section has a larger minHeight (400px) placeholder to maintain scroll
          position stability while content loads.
        </p>
      </div>
    ),
  },
};

export const WithTestId: Story = {
  args: {
    id: "endpoints",
    testId: "section-endpoints",
    eager: true,
    children: (
      <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Endpoints</h3>
        <p className="text-xs text-gray-600 dark:text-gray-400">
          This section has a data-testid attribute for E2E testing.
        </p>
        <div className="mt-2 flex gap-2">
          <span className="px-2 py-0.5 text-[10px] bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 rounded">production v3</span>
          <span className="px-2 py-0.5 text-[10px] bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400 rounded">staging v2</span>
        </div>
      </div>
    ),
  },
};

export const MultipleSections: Story = {
  render: () => (
    <div className="space-y-4 max-h-[400px] overflow-y-auto p-4 border border-gray-200 dark:border-gray-700 rounded-lg">
      {["configuration", "tools", "skills", "endpoints", "settings"].map((id, i) => (
        <LazySection key={id} id={id} eager={i === 0} minHeight={120}>
          <div className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg">
            <h4 className="text-xs font-semibold text-gray-700 dark:text-gray-300 capitalize">{id}</h4>
            <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
              Section content for {id}. Scroll to load lazy sections.
            </p>
          </div>
        </LazySection>
      ))}
    </div>
  ),
};
