import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Trash2, FileText, Loader2 } from "lucide-react";
import { formatDateTime } from "../../lib/date-format";

interface Document {
  key: string;
  filename: string;
  sizeBytes: number;
  lastModified: string;
}

interface Props {
  documents: Document[];
  onDelete: (documentKey: string) => Promise<void>;
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const size = bytes / Math.pow(1024, i);
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export default function KBDocumentTable({ documents, onDelete }: Props) {
  const { t } = useTranslation();
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [confirmKey, setConfirmKey] = useState<string | null>(null);

  const handleDelete = async (key: string) => {
    setDeletingKey(key);
    setConfirmKey(null);
    try {
      await onDelete(key);
    } finally {
      setDeletingKey(null);
    }
  };

  if (documents.length === 0) {
    return (
      <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-6 text-center">
        <FileText className="w-8 h-8 mx-auto mb-2 text-gray-300 dark:text-gray-600" />
        <p className="text-sm text-gray-500 dark:text-gray-400">
          No documents yet
        </p>
      </div>
    );
  }

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
            <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
              Filename
            </th>
            <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
              Size
            </th>
            <th className="text-left py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
              Uploaded
            </th>
            <th className="text-right py-2 px-3 text-xs font-medium text-gray-500 dark:text-gray-400">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr
              key={doc.key}
              className="border-b border-gray-100 dark:border-gray-800 last:border-0"
            >
              <td className="py-2 px-3 text-gray-900 dark:text-gray-100 font-mono text-xs">
                {doc.filename}
              </td>
              <td className="py-2 px-3 text-gray-600 dark:text-gray-400 text-xs">
                {formatFileSize(doc.sizeBytes)}
              </td>
              <td className="py-2 px-3 text-gray-500 dark:text-gray-400 text-xs">
                {formatDateTime(doc.lastModified)}
              </td>
              <td className="py-2 px-3 text-right">
                {confirmKey === doc.key ? (
                  <span className="inline-flex items-center gap-1">
                    <span className="text-[10px] text-gray-500 dark:text-gray-400">
                      {t("kb.deleteDocConfirm")}
                    </span>
                    <button
                      onClick={() => handleDelete(doc.key)}
                      disabled={deletingKey === doc.key}
                      className="text-[10px] px-1.5 py-0.5 bg-red-500 text-white rounded hover:bg-red-600 disabled:opacity-50"
                    >
                      {deletingKey === doc.key ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        t("common.confirm")
                      )}
                    </button>
                    <button
                      onClick={() => setConfirmKey(null)}
                      className="text-[10px] px-1.5 py-0.5 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded"
                    >
                      {t("common.cancel")}
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => setConfirmKey(doc.key)}
                    className="p-1 text-gray-400 dark:text-gray-500 hover:text-red-500 dark:hover:text-red-400 rounded hover:bg-gray-100 dark:hover:bg-gray-800"
                    title={t("kb.deleteDoc")}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
