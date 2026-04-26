import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { X, Trash2, Loader2 } from "lucide-react";
import { useMemoryStore } from "../../stores/memory-store";
import type { MemoryStrategy } from "../../lib/api-client";

interface MemoryDrawerProps {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  agentId: string;
}

export default function MemoryDrawer({ open, onClose, workspaceId, agentId }: MemoryDrawerProps) {
  const { t } = useTranslation();
  const { byAgent, fetchMemories, loadMore, deleteRecord, forgetAll } = useMemoryStore();
  const bucket = byAgent[agentId];
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [forgetAllConfirm, setForgetAllConfirm] = useState(false);
  const [forgetting, setForgetting] = useState(false);

  useEffect(() => {
    if (open && agentId) {
      fetchMemories(workspaceId, agentId);
    }
  }, [open, agentId, workspaceId, fetchMemories]);

  const handleDelete = async (recordId: string, strategy: MemoryStrategy) => {
    setDeletingId(recordId);
    try {
      await deleteRecord(workspaceId, agentId, recordId, strategy);
    } finally {
      setDeletingId(null);
    }
  };

  const handleForgetAll = async () => {
    setForgetting(true);
    try {
      await forgetAll(workspaceId, agentId);
      setForgetAllConfirm(false);
    } finally {
      setForgetting(false);
    }
  };

  const renderSection = (strategy: MemoryStrategy, titleKey: string) => {
    if (!bucket) return null;
    const section = bucket[strategy];
    const records = section.records;
    const hasMore = !!section.nextToken;
    const isLoadingMore = section.loadingMore;

    return (
      <div className="mb-6">
        <h3 className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-2 uppercase tracking-wide">
          {t(titleKey)}
        </h3>
        {records.length === 0 && (
          <p className="text-xs text-gray-400 dark:text-gray-500">{t("memory.emptyState")}</p>
        )}
        {records.length > 0 && (
          <div className="space-y-2">
            {records.map((record) => (
              <div
                key={record.id}
                className="flex items-start gap-2 p-2 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-800"
              >
                <p className="flex-1 text-[13px] text-gray-700 dark:text-gray-300 min-w-0">
                  {String(record.content)}
                </p>
                <button
                  type="button"
                  onClick={() => handleDelete(record.id, strategy)}
                  disabled={deletingId === record.id}
                  className="p-1 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 transition-colors disabled:opacity-50"
                  aria-label={t("memory.delete")}
                >
                  {deletingId === record.id ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="w-3.5 h-3.5" />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
        {hasMore && (
          <button
            type="button"
            onClick={() => loadMore(workspaceId, agentId, strategy)}
            disabled={isLoadingMore}
            className="mt-2 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 font-medium disabled:opacity-50"
          >
            {isLoadingMore ? t("memory.loading") : t("memory.loadMore")}
          </button>
        )}
      </div>
    );
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="fixed right-0 top-0 bottom-0 w-[400px] bg-white dark:bg-gray-950 border-l border-gray-200 dark:border-gray-800 shadow-xl z-50 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {t("memory.drawerTitle")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            aria-label={t("memory.close")}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {bucket?.loading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400 dark:text-gray-500" />
              <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">
                {t("memory.loading")}
              </span>
            </div>
          )}
          {!bucket?.loading && (
            <>
              {renderSection("preferences", "memory.section.preferences")}
              {renderSection("facts", "memory.section.facts")}
              {renderSection("summaries", "memory.section.summaries")}
              {renderSection("episodes", "memory.section.episodes")}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-800">
          {!forgetAllConfirm ? (
            <button
              type="button"
              onClick={() => setForgetAllConfirm(true)}
              className="w-full px-3 py-2 text-xs font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/40 transition-colors"
            >
              {t("memory.forgetAll")}
            </button>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-600 dark:text-gray-400">
                {t("memory.forgetAllConfirm")}
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setForgetAllConfirm(false)}
                  className="flex-1 px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                >
                  {t("memory.cancel")}
                </button>
                <button
                  type="button"
                  onClick={handleForgetAll}
                  disabled={forgetting}
                  className="flex-1 px-3 py-2 text-xs font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-50 transition-colors"
                >
                  {forgetting ? (
                    <span className="flex items-center justify-center">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    </span>
                  ) : (
                    t("memory.forgetAllDoIt")
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
