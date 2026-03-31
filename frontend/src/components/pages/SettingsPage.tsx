import { Settings, Moon, Sun, User, Download } from "lucide-react";

export default function SettingsPage() {
  return (
    <div className="max-w-2xl mx-auto p-8">
      <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2 mb-6">
        <Settings className="w-5 h-5" /> Settings
      </h2>

      <div className="space-y-6">
        {/* Default Model */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Default Model</h3>
          <select className="w-full px-3 py-2 rounded-lg border border-gray-300 text-sm">
            <option value="us.anthropic.claude-sonnet-4-20250514-v1:0">Claude Sonnet 4</option>
            <option value="us.anthropic.claude-haiku-4-5-20251001-v1:0">Claude Haiku 4.5</option>
            <option value="us.amazon.nova-pro-v1:0">Amazon Nova Pro</option>
          </select>
        </section>

        {/* Theme */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3">Theme</h3>
          <div className="flex gap-3">
            <button className="flex items-center gap-2 px-4 py-2 rounded-lg border border-blue-500 bg-blue-50 text-sm text-blue-700">
              <Sun className="w-4 h-4" /> Light
            </button>
            <button className="flex items-center gap-2 px-4 py-2 rounded-lg border border-gray-200 text-sm text-gray-600 hover:bg-gray-50">
              <Moon className="w-4 h-4" /> Dark
            </button>
          </div>
        </section>

        {/* Profile */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
            <User className="w-4 h-4" /> Profile
          </h3>
          <p className="text-sm text-gray-500">Managed by Cognito. Sign out and sign in to switch accounts.</p>
        </section>

        {/* Export */}
        <section className="border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-700 mb-3 flex items-center gap-2">
            <Download className="w-4 h-4" /> Export
          </h3>
          <button className="px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-600 hover:bg-gray-50">
            Export All Agent Configs
          </button>
        </section>
      </div>
    </div>
  );
}
