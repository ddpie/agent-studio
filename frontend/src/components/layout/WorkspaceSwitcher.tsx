import { useEffect, useRef, useState } from "react";
import { ChevronDown, Check, Briefcase } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useWorkspaceStore } from "../../stores/workspace-store";

export default function WorkspaceSwitcher() {
  const { t } = useTranslation();
  const { currentWorkspace, workspaces, switchWorkspace } = useWorkspaceStore();
  const [open, setOpen] = useState(false);
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
  const hasMultiple = workspaces.length > 1;

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => hasMultiple && setOpen((v) => !v)}
        disabled={!hasMultiple}
        title={hasMultiple ? t("workspace.switcherTitle") : label}
        className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs bg-gray-800 border border-gray-700 text-gray-100 max-w-[220px] ${
          hasMultiple ? "hover:bg-gray-700 cursor-pointer" : "cursor-default opacity-80"
        }`}
      >
        <Briefcase className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
        <span className="truncate">{label}</span>
        {hasMultiple && <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />}
      </button>

      {open && hasMultiple && (
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
        </div>
      )}
    </div>
  );
}
