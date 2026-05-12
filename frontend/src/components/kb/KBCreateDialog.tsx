import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, description: string) => Promise<string>;
}

export default function KBCreateDialog({ open, onClose, onCreate }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await onCreate(name.trim(), description.trim());
    } catch (e: any) {
      setError(e.message || "Failed to create knowledge base");
      setCreating(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
            {t("kb.create")}
          </p>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 rounded"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="text-[11px] text-gray-500 dark:text-gray-400 mb-1 block">
              {t("kb.name")} *
            </label>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim()) handleSubmit();
                if (e.key === "Escape") onClose();
              }}
              placeholder={t("kb.name")}
              className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="text-[11px] text-gray-500 dark:text-gray-400 mb-1 block">
              {t("kb.description")}
            </label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim()) handleSubmit();
                if (e.key === "Escape") onClose();
              }}
              placeholder={t("kb.description")}
              className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>

        {error && (
          <p className="mt-2 text-xs text-red-500 dark:text-red-400">{error}</p>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!name.trim() || creating}
            className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
          >
            {creating ? (
              <span className="flex items-center gap-1">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                {t("kb.creating")}
              </span>
            ) : (
              t("common.create")
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
