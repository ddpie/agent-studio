import { useRef } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import DetailSideNav from "../DetailSideNav";
import type { NavEntry } from "../DetailSideNav";
import { Bot, Wrench, FileText, Settings, Zap, Shield } from "lucide-react";

const meta: Meta<typeof DetailSideNav> = {
  title: "Agents/DetailSideNav",
  component: DetailSideNav,
  decorators: [
    (Story) => (
      <div className="flex h-[400px] bg-white dark:bg-gray-900 p-4">
        <Story />
        <div className="flex-1 border-l border-gray-200 dark:border-gray-700 pl-4">
          <p className="text-sm text-gray-500 dark:text-gray-400">Main content area</p>
        </div>
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof DetailSideNav>;

const navItems: NavEntry[] = [
  { id: "overview", label: "Overview", icon: <Bot className="w-3.5 h-3.5" /> },
  { id: "tools", label: "Tools", icon: <Wrench className="w-3.5 h-3.5" /> },
  { id: "prompt", label: "System Prompt", icon: <FileText className="w-3.5 h-3.5" /> },
  { id: "skills", label: "Skills", icon: <Zap className="w-3.5 h-3.5" /> },
  { id: "settings", label: "Settings", icon: <Settings className="w-3.5 h-3.5" /> },
];

const navWithGroups: NavEntry[] = [
  { id: "overview", label: "Overview", icon: <Bot className="w-3.5 h-3.5" /> },
  { id: "prompt", label: "System Prompt", icon: <FileText className="w-3.5 h-3.5" /> },
  {
    type: "group",
    id: "configuration",
    label: "Configuration",
    items: [
      { id: "tools", label: "Tools", icon: <Wrench className="w-3.5 h-3.5" /> },
      { id: "skills", label: "Skills", icon: <Zap className="w-3.5 h-3.5" /> },
      { id: "security", label: "Security", icon: <Shield className="w-3.5 h-3.5" /> },
    ],
  },
  { id: "settings", label: "Settings", icon: <Settings className="w-3.5 h-3.5" /> },
];

function NavWrapper({ items, activeId }: { items: NavEntry[]; activeId: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={scrollRef} className="flex h-full">
      <DetailSideNav
        items={items}
        activeId={activeId}
        scrollRootRef={scrollRef}
        memoryKey="storybook-agent-1"
        onNavigate={fn()}
      />
    </div>
  );
}

export const Default: Story = {
  render: () => <NavWrapper items={navItems} activeId="overview" />,
};

export const ActiveTools: Story = {
  render: () => <NavWrapper items={navItems} activeId="tools" />,
};

export const WithGroups: Story = {
  render: () => <NavWrapper items={navWithGroups} activeId="prompt" />,
};

export const NoActiveItem: Story = {
  render: () => <NavWrapper items={navItems} activeId="" />,
};
