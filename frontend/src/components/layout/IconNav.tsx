// src/components/layout/IconNav.tsx
import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import { Bot, Package, Wrench, Plug, Store, Settings } from "lucide-react";

const navItems = [
  { to: "/agents", icon: Bot, labelKey: "nav.agents" },
  { to: "/skills", icon: Package, labelKey: "nav.skills" },
  { to: "/tools", icon: Wrench, labelKey: "nav.tools" },
  { to: "/mcp", icon: Plug, labelKey: "nav.mcp" },
  { to: "/marketplace", icon: Store, labelKey: "nav.marketplace" },
] as const;

export default function IconNav() {
  const { t } = useTranslation();
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `w-10 h-10 flex items-center justify-center rounded-lg transition-colors ${
      isActive ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white hover:bg-gray-800"
    }`;

  return (
    <nav className="flex flex-col items-center w-12 bg-gray-900 py-3 gap-1 flex-shrink-0">
      {navItems.map(({ to, icon: Icon, labelKey }) => (
        <NavLink key={to} to={to} className={linkClass} title={t(labelKey)}>
          <Icon className="w-5 h-5" />
        </NavLink>
      ))}
      <div className="mt-auto">
        <NavLink to="/settings" className={linkClass} title={t("nav.settings")}>
          <Settings className="w-5 h-5" />
        </NavLink>
      </div>
    </nav>
  );
}
