import { Settings, Download, Trash2, Database, Shield, Sun, Moon, Monitor, Languages, Users, UserCog } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useUISettings } from "../../stores/ui-settings-store";
import ConfirmDialog from "../ui/ConfirmDialog";
import WorkspaceMembersTab from "./WorkspaceMembersTab";
import AccountSettingsTab from "./AccountSettingsTab";

type TabId = "general" | "account" | "workspace";

export default function SettingsPage() {
  const { t } = useTranslation();
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [tab, setTab] = useState<TabId>("general");
  const { sidebarWidth, inputHeight, theme, language, setSidebarWidth, setInputHeight, setTheme, setLanguage } = useUISettings();

  const clearLocalStorage = () => {
    localStorage.clear();
    window.location.reload();
  };

  const exportAgents = () => {
    // Export all agent configs from localStorage sessions
    const chatData = localStorage.getItem("agent-studio-chat");
    const uiData = localStorage.getItem("agent-studio-ui");
    const blob = new Blob([JSON.stringify({ chat: chatData, ui: uiData }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `agent-studio-export-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-3xl mx-auto p-6 overflow-y-auto">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-5">
        <Settings className="w-4 h-4" /> {t("settings.title")}
      </h2>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4">
        {([
          { id: "general" as const, icon: Settings, label: t("settings.tabGeneral") },
          { id: "account" as const, icon: UserCog, label: t("settings.tabAccount") },
          { id: "workspace" as const, icon: Users, label: t("settings.tabWorkspace") },
        ]).map(({ id, icon: Icon, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs border-b-2 -mb-px transition-colors ${
              tab === id
                ? "border-blue-500 text-blue-600 dark:text-blue-400"
                : "border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            }`}
          >
            <Icon className="w-3.5 h-3.5" /> {label}
          </button>
        ))}
      </div>

      {tab === "account" && <AccountSettingsTab />}

      {tab === "workspace" && <WorkspaceMembersTab />}

      {tab === "general" && (
        <div className="space-y-4">
          {/* UI Preferences */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">{t("settings.interface")}</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-gray-700 dark:text-gray-300">{t("settings.sidebarWidth")}</p>
                  <p className="text-[10px] text-gray-400">{t("settings.currentValue", { value: sidebarWidth })}</p>
                </div>
                <button
                  onClick={() => setSidebarWidth(224)}
                  className="text-[10px] px-2 py-1 border border-gray-200 dark:border-gray-700 rounded text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  {t("common.reset")}
                </button>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-gray-700 dark:text-gray-300">{t("settings.inputHeight")}</p>
                  <p className="text-[10px] text-gray-400">{t("settings.currentValue", { value: inputHeight })}</p>
                </div>
                <button
                  onClick={() => setInputHeight(44)}
                  className="text-[10px] px-2 py-1 border border-gray-200 dark:border-gray-700 rounded text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  {t("common.reset")}
                </button>
              </div>
            </div>
          </section>

          {/* Theme */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">{t("settings.theme")}</h3>
            <div className="flex gap-2">
              {([
                { id: "light" as const, icon: Sun, label: t("settings.light") },
                { id: "dark" as const, icon: Moon, label: t("settings.dark") },
                { id: "system" as const, icon: Monitor, label: t("settings.system") },
              ]).map(({ id, icon: Icon, label }) => (
                <button
                  key={id}
                  onClick={() => setTheme(id)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                    theme === id
                      ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                      : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" /> {label}
                </button>
              ))}
            </div>
          </section>

          {/* Language */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
              <Languages className="w-3.5 h-3.5" /> {t("settings.language")}
            </h3>
            <p className="text-[10px] text-gray-400 mb-2">{t("settings.languageDesc")}</p>
            <div className="flex gap-2">
              {([
                { id: "zh" as const, label: "中文" },
                { id: "en" as const, label: "English" },
              ]).map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setLanguage(id)}
                  className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                    language === id
                      ? "border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
                      : "border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </section>


          {/* Infrastructure Info */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
              <Database className="w-3.5 h-3.5" /> {t("settings.infrastructure")}
            </h3>
            <div className="space-y-1.5 text-xs text-gray-500">
              <div className="flex justify-between">
                <span>{t("settings.region")}</span>
                <span className="font-mono text-gray-700 dark:text-gray-300">{import.meta.env.VITE_AGENTCORE_REGION || "—"}</span>
              </div>
              <div className="flex justify-between">
                <span>{t("settings.storage")}</span>
                <span className="font-mono text-gray-700 dark:text-gray-300">S3 + DynamoDB</span>
              </div>
              <div className="flex justify-between">
                <span>{t("settings.auth")}</span>
                <span className="font-mono text-gray-700 dark:text-gray-300">Cognito</span>
              </div>
            </div>
          </section>

          {/* Data */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3 flex items-center gap-1.5">
              <Shield className="w-3.5 h-3.5" /> {t("settings.data")}
            </h3>
            <div className="flex gap-2">
              <button
                onClick={exportAgents}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                <Download className="w-3.5 h-3.5" /> {t("settings.exportLocal")}
              </button>
              <button
                onClick={() => setShowClearConfirm(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 rounded-lg text-red-600 hover:bg-red-50"
              >
                <Trash2 className="w-3.5 h-3.5" /> {t("settings.clearLocal")}
              </button>
            </div>
          </section>
        </div>
      )}

      <ConfirmDialog
        open={showClearConfirm}
        title={t("settings.clearLocal")}
        message={t("settings.clearConfirm")}
        confirmLabel={t("common.delete")}
        onConfirm={() => { setShowClearConfirm(false); clearLocalStorage(); }}
        onCancel={() => setShowClearConfirm(false)}
        danger
      />
    </div>
  );
}
