// src/components/layout/IconNav.tsx
import { NavLink } from "react-router";
import { Bot, Package, Plug, Settings } from "lucide-react";

const navItems = [
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/skills", icon: Package, label: "Skills" },
  { to: "/mcp", icon: Plug, label: "MCP" },
] as const;

export default function IconNav() {
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `w-10 h-10 flex items-center justify-center rounded-lg transition-colors ${
      isActive ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-gray-800"
    }`;

  return (
    <nav className="flex flex-col items-center w-12 bg-gray-900 py-3 gap-1 flex-shrink-0">
      {navItems.map(({ to, icon: Icon, label }) => (
        <NavLink key={to} to={to} className={linkClass} title={label}>
          <Icon className="w-5 h-5" />
        </NavLink>
      ))}
      <div className="mt-auto">
        <NavLink to="/settings" className={linkClass} title="Settings">
          <Settings className="w-5 h-5" />
        </NavLink>
      </div>
    </nav>
  );
}
