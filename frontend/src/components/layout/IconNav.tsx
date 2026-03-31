import { useNavStore } from "../../stores/nav-store";
import { Bot, Package, Plug, Settings } from "lucide-react";

const navItems = [
  { id: "agents" as const, icon: Bot, label: "Agents" },
  { id: "skills" as const, icon: Package, label: "Skills" },
  { id: "mcp" as const, icon: Plug, label: "MCP" },
] as const;

export default function IconNav() {
  const { activeSection, setSection } = useNavStore();

  return (
    <nav className="flex flex-col items-center w-12 bg-gray-900 py-3 gap-1 flex-shrink-0">
      {navItems.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          onClick={() => setSection(id)}
          title={label}
          className={`w-10 h-10 flex items-center justify-center rounded-lg transition-colors ${
            activeSection === id
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white hover:bg-gray-800"
          }`}
        >
          <Icon className="w-5 h-5" />
        </button>
      ))}

      {/* Settings at bottom */}
      <div className="mt-auto">
        <button
          onClick={() => setSection("settings")}
          title="Settings"
          className={`w-10 h-10 flex items-center justify-center rounded-lg transition-colors ${
            activeSection === "settings"
              ? "bg-blue-600 text-white"
              : "text-gray-400 hover:text-white hover:bg-gray-800"
          }`}
        >
          <Settings className="w-5 h-5" />
        </button>
      </div>
    </nav>
  );
}
