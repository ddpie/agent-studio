import { useEffect, useRef, useState } from "react";
import { ChevronDown, Check, Briefcase, Plus, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "../../lib/toast";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { createWorkspace, setWorkspaceId, ApiError } from "../../lib/api-client";
import { demoteHashForWorkspaceSwitch } from "../../lib/workspace-switch-url";

export default function WorkspaceSwitcher() {
  const { t } = useTranslation();
  const { currentWorkspace, workspaces, switchWorkspace, refreshWorkspaces } =
    useWorkspaceStore();
  const [open, setOpen] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const label = currentWorkspace?.name || currentWorkspace?.workspaceId || t("workspace.unnamed");

  async function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error(t("workspace.create.nameRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const ws = await createWorkspace(trimmed, description.trim() || undefined);
      toast.success(t("workspace.create.success"));
      setShowCreate(false);
      setName("");
      setDescription("");
      setOpen(false);
      await refreshWorkspaces();
      // Switch into the newly created workspace (triggers hard reload).
      // Await setWorkspaceId so the chat-store's localStorage clear (which
      // it schedules via dynamic import) finishes before the reload, else
      // the old workspace's chat history leaks across.
      await setWorkspaceId(ws.workspaceId);
      const nextHash = demoteHashForWorkspaceSwitch(window.location.hash);
      if (nextHash !== window.location.hash) {
        window.location.hash = nextHash;
      }
      window.location.reload();
    } catch (err) {
      if (err instanceof ApiError && err.body?.code === "WORKSPACE_NAME_DUPLICATE") {
        toast.error(t("workspace.create.nameDuplicate"));
      } else {
        const msg = err instanceof ApiError ? err.body?.error : undefined;
        toast.error(msg || t("workspace.create.failed"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div ref={wrapperRef} className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          title={t("workspace.switcherTitle")}
          data-testid="workspace-switcher-btn"
          className="flex items-center gap-1.5 px-2 py-1 rounded text-xs bg-gray-800 border border-gray-700 text-gray-100 max-w-[220px] hover:bg-gray-700 cursor-pointer"
        >
          <Briefcase className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
          <span className="truncate">{label}</span>
          <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
        </button>

        {open && (
          <div className="absolute top-full left-0 mt-1 w-64 rounded-lg shadow-xl bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 z-50 overflow-hidden">
            <div className="px-3 py-2 text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide border-b border-gray-100 dark:border-gray-800">
              {t("workspace.switcherTitle")}
            </div>
            <ul className="max-h-80 overflow-auto py-1">
              {workspaces.map((ws) => {
                const isCurrent = ws.workspaceId === currentWorkspace?.workspaceId;
                return (
                  <li key={ws.workspaceId}>
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        if (!isCurrent) switchWorkspace(ws.workspaceId);
                      }}
                      className={`w-full flex items-center justify-between gap-2 px-3 py-2 text-xs text-left ${
                        isCurrent
                          ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-200"
                          : "text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800"
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">
                          {ws.name || t("workspace.unnamed")}
                        </div>
                        <div className="truncate text-[10px] text-gray-500 dark:text-gray-400">
                          {t(`workspace.role.${ws.role}`)}
                        </div>
                      </div>
                      {isCurrent && <Check className="w-3.5 h-3.5 text-blue-600 dark:text-blue-300 flex-shrink-0" />}
                    </button>
                  </li>
                );
              })}
            </ul>
            <div className="border-t border-gray-100 dark:border-gray-800">
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  setShowCreate(true);
                }}
                data-testid="create-workspace-btn"
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-left text-blue-600 dark:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/20"
              >
                <Plus className="w-3.5 h-3.5" />
                {t("workspace.create.button")}
              </button>
            </div>
          </div>
        )}
      </div>

      {showCreate && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50"
          onClick={() => !submitting && setShowCreate(false)}
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-[420px] max-w-[92vw]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-gray-100 dark:border-gray-800">
              <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {t("workspace.create.dialogTitle")}
              </h3>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                {t("workspace.create.dialogSubtitle")}
              </p>
            </div>
            <div className="px-5 py-4 space-y-3">
              <label className="block">
                <span className="text-xs font-medium text-gray-700 dark:text-gray-200">
                  {t("workspace.create.nameLabel")}
                </span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("workspace.create.namePlaceholder")}
                  maxLength={100}
                  autoFocus
                  data-testid="create-workspace-name"
                  className="mt-1 w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-gray-700 dark:text-gray-200">
                  {t("workspace.create.descriptionLabel")}
                </span>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={3}
                  maxLength={500}
                  className="mt-1 w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                />
              </label>
            </div>
            <div className="px-5 py-3 border-t border-gray-100 dark:border-gray-800 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowCreate(false)}
                disabled={submitting}
                className="px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
              >
                {t("workspace.create.cancel")}
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={submitting || !name.trim()}
                data-testid="create-workspace-submit"
                className="px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-1.5"
              >
                {submitting && <Loader2 className="w-3 h-3 animate-spin" />}
                {submitting ? t("workspace.create.creating") : t("workspace.create.submit")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
