import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { FolderClosed, File } from "lucide-react";
import useIsDark from "../../hooks/useIsDark";
import ConfirmDialog from "../ui/ConfirmDialog";

export type DialogType = "newFile" | "newFolder" | "rename" | "move" | "deleteFile";

export interface DialogState {
  type: DialogType;
  context: { parentDir?: string; path?: string; currentName?: string };
}

interface SkillFileDialogsProps {
  activeDialog: DialogState | null;
  availableDirs: string[];
  onClose: () => void;
  onConfirm: (type: DialogType, value: string) => void;
}

export default function SkillFileDialogs({ activeDialog, availableDirs, onClose, onConfirm }: SkillFileDialogsProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const [dialogInput, setDialogInput] = useState("");

  useEffect(() => {
    if (!activeDialog) return;
    if (activeDialog.type === "rename" && activeDialog.context.currentName) {
      setDialogInput(activeDialog.context.currentName);
    } else {
      setDialogInput("");
    }
  }, [activeDialog]);

  if (!activeDialog) return null;

  if (activeDialog.type === "deleteFile") {
    return (
      <ConfirmDialog
        open
        title={t("skillEditor.deleteFile")}
        message={t("skillEditor.deleteFileDesc", { name: activeDialog.context.path })}
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        danger
        onConfirm={() => onConfirm("deleteFile", activeDialog.context.path!)}
        onCancel={onClose}
      />
    );
  }

  if (activeDialog.type === "move") {
    const currentDir = activeDialog.context.path?.includes("/")
      ? activeDialog.context.path.slice(0, activeDialog.context.path.indexOf("/"))
      : "(root)";
    return (
      <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={onClose}>
        <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
          <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.moveFile")}</p>
          <p className="text-xs text-gray-500 mb-3">
            {t("skillEditor.moveTo")} <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{activeDialog.context.path?.split("/").pop()}</span>
          </p>
          <div className="space-y-1 max-h-40 overflow-y-auto mb-3">
            {availableDirs.map(dir => {
              const isCurrent = dir === currentDir;
              return (
                <button key={dir} onClick={() => setDialogInput(dir)}
                  className={`w-full text-left px-2.5 py-1.5 text-xs rounded-lg flex items-center gap-2 ${
                    dialogInput === dir ? "bg-blue-600 text-white"
                      : isCurrent ? isDark ? "bg-gray-700 text-gray-400" : "bg-gray-100 text-gray-400"
                      : isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"
                  }`}>
                  {dir === "(root)" ? <File className="w-3 h-3" /> : <FolderClosed className="w-3 h-3 text-yellow-500" />}
                  {dir === "(root)" ? t("skillEditor.moveToRoot") : dir}
                  {isCurrent && <span className="text-[9px] ml-auto opacity-60">{t("skillEditor.moveCurrent")}</span>}
                </button>
              );
            })}
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
            <button onClick={() => onConfirm("move", dialogInput)} disabled={!dialogInput} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("skillEditor.moveFile")}</button>
          </div>
        </div>
      </div>
    );
  }

  // newFile, newFolder, rename — all use a text input dialog
  const titles: Record<string, string> = {
    newFile: activeDialog.context.parentDir ? t("skillEditor.newFileIn", { dir: activeDialog.context.parentDir }) : t("skillEditor.newFileRoot"),
    newFolder: t("skillEditor.newFolderTitle"),
    rename: t("skillEditor.renameFile"),
  };
  const placeholders: Record<string, string> = {
    newFile: t("skillEditor.filenamePlaceholder"),
    newFolder: t("skillEditor.folderPlaceholder"),
    rename: "",
  };
  const confirmLabels: Record<string, string> = {
    newFile: t("common.create"),
    newFolder: t("common.create"),
    rename: t("common.rename"),
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={onClose}>
      <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
        <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{titles[activeDialog.type]}</p>
        <input
          autoFocus
          value={dialogInput}
          onChange={e => setDialogInput(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && dialogInput.trim()) onConfirm(activeDialog.type, dialogInput.trim()); if (e.key === "Escape") onClose(); }}
          placeholder={placeholders[activeDialog.type]}
          className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
        />
        <div className="flex justify-end gap-2 mt-3">
          <button onClick={onClose} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
          <button onClick={() => onConfirm(activeDialog.type, dialogInput.trim())} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{confirmLabels[activeDialog.type]}</button>
        </div>
      </div>
    </div>
  );
}
