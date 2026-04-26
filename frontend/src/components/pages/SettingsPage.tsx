import { Settings, Download, Trash2, Shield, Sun, Moon, Monitor, Languages, Users, UserCog, KeyRound } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useUISettings } from "../../stores/ui-settings-store";
import { useWorkspaceStore } from "../../stores/workspace-store";
import ConfirmDialog from "../ui/ConfirmDialog";
import WorkspaceMembersTab from "./WorkspaceMembersTab";
import WorkspaceSettingsTab from "./WorkspaceSettingsTab";
import AccountSettingsTab from "./AccountSettingsTab";
import IamPermissionsTab from "./IamPermissionsTab";

type TabId = "general" | "account" | "workspace" | "iam";

const VALID_TABS: TabId[] = ["general", "account", "workspace", "iam"];

export default function SettingsPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const { currentWorkspace } = useWorkspaceStore();
  const role = currentWorkspace?.role;
  const canSeeIam = role === "admin" || role === "owner";
  // URL is the source of truth for the active tab. ?tab=workspace deep-
  // links to the workspace tab (Kiro-key banner CTA), and clicking a
  // tab header writes back with `replace:true` so we don't flood the
  // history stack with one entry per click.
  const rawTab = searchParams.get("tab") as TabId | null;
  const tab: TabId = rawTab && VALID_TABS.includes(rawTab)
    ? rawTab
    : "general";
  const setTab = (id: TabId) => {
    const next = new URLSearchParams(searchParams);
    // Keep the default (general) out of the URL so /settings stays
    // canonical — only non-default tabs surface in the query string.
    if (id === "general") next.delete("tab");
    else next.set("tab", id);
    setSearchParams(next, { replace: true });
  };
  const { sidebarWidth, inputHeight, theme, language, showInlineToolCalls, setSidebarWidth, setInputHeight, setTheme, setLanguage, setShowInlineToolCalls } = useUISettings();

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
    <div className="max-w-4xl p-6 overflow-y-auto">
      <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2 mb-5">
        <Settings className="w-4 h-4" /> {t("settings.title")}
      </h2>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 dark:border-gray-700 mb-4">
        {([
          { id: "general" as const, icon: Settings, label: t("settings.tabGeneral") },
          { id: "account" as const, icon: UserCog, label: t("settings.tabAccount") },
          { id: "workspace" as const, icon: Users, label: t("settings.tabWorkspace") },
          ...(canSeeIam ? [{ id: "iam" as const, icon: KeyRound, label: t("settings.tabIam") }] : []),
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

      {tab === "iam" && canSeeIam && <IamPermissionsTab />}

      {tab === "account" && <AccountSettingsTab />}

      {tab === "workspace" && (
        <div className="space-y-6">
          <WorkspaceSettingsTab />
          <WorkspaceMembersTab />
        </div>
      )}

      {tab === "general" && (
        <div className="space-y-4">
          {/* UI Preferences */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">{t("settings.interface")}</h3>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-gray-700 dark:text-gray-300">{t("settings.sidebarWidth")}</p>
                  <p className="text-[10px] text-gray-400 dark:text-gray-500">{t("settings.currentValue", { value: sidebarWidth })}</p>
                </div>
                <button
                  onClick={() => setSidebarWidth(224)}
                  className="text-[10px] px-2 py-1 border border-gray-200 dark:border-gray-700 rounded text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  {t("common.reset")}
                </button>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium text-gray-700 dark:text-gray-300">{t("settings.inputHeight")}</p>
                  <p className="text-[10px] text-gray-400 dark:text-gray-500">{t("settings.currentValue", { value: inputHeight })}</p>
                </div>
                <button
                  onClick={() => setInputHeight(44)}
                  className="text-[10px] px-2 py-1 border border-gray-200 dark:border-gray-700 rounded text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
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
            <p className="text-[10px] text-gray-400 dark:text-gray-500 mb-2">{t("settings.languageDesc")}</p>
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

          {/* Chat UI */}
          <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide mb-3">
              {t("settings.chatUi", "Chat")}
            </h3>
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
                  {t("settings.showInlineToolCalls", "Show tool calls inline")}
                </p>
                <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
                  {t("settings.showInlineToolCallsHint", "Interleave tool invocations with the assistant's text. Turn off to stack them at the end of the message.")}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={showInlineToolCalls}
                onClick={() => setShowInlineToolCalls(!showInlineToolCalls)}
                className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${showInlineToolCalls ? "bg-blue-600" : "bg-gray-200 dark:bg-gray-700"}`}
              >
                <span
                  aria-hidden="true"
                  className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition-transform ${showInlineToolCalls ? "translate-x-4" : "translate-x-0"}`}
                />
              </button>
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
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 dark:border-red-900/60 rounded-lg text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
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
