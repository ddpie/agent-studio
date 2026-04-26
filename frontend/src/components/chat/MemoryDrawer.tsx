import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { X, Trash2, Loader2, Heart, BookOpen, MessageSquare, Clapperboard, ChevronDown, ChevronRight } from "lucide-react";
import { useMemoryStore } from "../../stores/memory-store";
import type { MemoryRecord, MemoryStrategy } from "../../lib/api-client";

interface MemoryDrawerProps {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  agentId: string;
}

const SECTION_ICONS: Record<MemoryStrategy, typeof Heart> = {
  preferences: Heart,
  facts: BookOpen,
  summaries: MessageSquare,
  episodes: Clapperboard,
};

function extractText(content: MemoryRecord["content"]): { main: string; context?: string; tags?: string[] } {
  if (typeof content === "string") return { main: content };
  const obj = content as Record<string, unknown>;

  // Preference records: { preference, context, categories }
  if (obj.preference && typeof obj.preference === "string") {
    return {
      main: obj.preference as string,
      context: (obj.context as string) || undefined,
      tags: Array.isArray(obj.categories) ? (obj.categories as string[]) : undefined,
    };
  }

  // Plain text records (facts, summaries)
  if (obj.text && typeof obj.text === "string") {
    let text = obj.text as string;
    // Summaries may contain XML <topic> tags — strip them for cleaner display
    text = text.replace(/<\/?topic[^>]*>/g, "").trim();
    return { main: text };
  }

  return { main: JSON.stringify(content) };
}

function formatRelativeDate(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))}m`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`;
  return `${Math.floor(diff / 86_400_000)}d`;
}

function RecordCard({
  record,
  deleting,
  onDelete,
  t,
}: {
  record: MemoryRecord;
  deleting: boolean;
  onDelete: () => void;
  t: (k: string) => string;
}) {
  const { main, context, tags } = extractText(record.content);

  return (
    <div className="group relative p-2.5 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-800 hover:border-gray-200 dark:hover:border-gray-700 transition-colors">
      <div className="flex items-start gap-2">
        <p className="flex-1 text-[13px] leading-relaxed text-gray-800 dark:text-gray-200 min-w-0">
          {main}
        </p>
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          className="shrink-0 p-1 text-gray-300 dark:text-gray-700 opacity-0 group-hover:opacity-100 hover:text-red-500 dark:hover:text-red-400 transition-all disabled:opacity-50"
          aria-label={t("memory.delete")}
        >
          {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
        </button>
      </div>
      {context && (
        <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500 leading-snug">{context}</p>
      )}
      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
        {tags?.map((tag) => (
          <span
            key={tag}
            className="px-1.5 py-0.5 text-[10px] font-medium bg-blue-50 dark:bg-blue-950/30 text-blue-600 dark:text-blue-400 rounded"
          >
            {tag}
          </span>
        ))}
        {record.createdAt && (
          <span className="text-[10px] text-gray-300 dark:text-gray-600 ml-auto">
            {formatRelativeDate(record.createdAt as unknown as string)}
          </span>
        )}
      </div>
    </div>
  );
}

export default function MemoryDrawer({ open, onClose, workspaceId, agentId }: MemoryDrawerProps) {
  const { t } = useTranslation();
  const { byAgent, fetchMemories, loadMore, deleteRecord, forgetAll } = useMemoryStore();
  const bucket = byAgent[agentId];
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [forgetAllConfirm, setForgetAllConfirm] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const [collapsedSections, setCollapsedSections] = useState<Set<MemoryStrategy>>(new Set());

  const toggleSection = (s: MemoryStrategy) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s);
      else next.add(s);
      return next;
    });
  };

  useEffect(() => {
    if (open && agentId) fetchMemories(workspaceId, agentId);
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

  const totalRecords = bucket
    ? (["preferences", "facts", "summaries", "episodes"] as const).reduce(
        (sum, s) => sum + (bucket[s]?.records?.length ?? 0), 0)
    : 0;

  const renderSection = (strategy: MemoryStrategy, titleKey: string) => {
    if (!bucket) return null;
    const section = bucket[strategy];
    const records = section.records;
    const count = records.length;
    const Icon = SECTION_ICONS[strategy];
    const collapsed = collapsedSections.has(strategy);

    return (
      <div className="mb-4">
        <button
          type="button"
          onClick={() => toggleSection(strategy)}
          className="w-full flex items-center gap-1.5 py-1.5 group"
        >
          {collapsed
            ? <ChevronRight className="w-3 h-3 text-gray-400 dark:text-gray-500" />
            : <ChevronDown className="w-3 h-3 text-gray-400 dark:text-gray-500" />
          }
          <Icon className="w-3.5 h-3.5 text-gray-400 dark:text-gray-500" />
          <h3 className="text-xs font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">
            {t(titleKey)}
          </h3>
          {count > 0 && (
            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 rounded-full">
              {count}
            </span>
          )}
        </button>
        {!collapsed && (
          <div className="pl-5 mt-1">
            <p className="text-[11px] text-gray-400 dark:text-gray-500 mb-2">{t(`memory.section.${strategy}Hint`)}</p>
            {count === 0 ? (
              <p className="text-[11px] text-gray-400 dark:text-gray-500 italic">{t("memory.emptyState")}</p>
            ) : (
              <div className="space-y-1.5">
                {records.map((record) => (
                  <RecordCard
                    key={record.id}
                    record={record}
                    deleting={deletingId === record.id}
                    onDelete={() => handleDelete(record.id, strategy)}
                    t={t}
                  />
                ))}
              </div>
            )}
            {section.nextToken && (
              <button
                type="button"
                onClick={() => loadMore(workspaceId, agentId, strategy)}
                disabled={section.loadingMore}
                className="mt-2 text-[11px] text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 font-medium disabled:opacity-50"
              >
                {section.loadingMore ? t("memory.loading") : t("memory.loadMore")}
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  if (!open) return null;

  return (
    <>
      <div className="fixed inset-0 bg-black/20 dark:bg-black/40 z-40" onClick={onClose} />
      <div className="fixed right-0 top-0 bottom-0 w-[420px] bg-white dark:bg-gray-950 border-l border-gray-200 dark:border-gray-800 shadow-xl z-50 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-800">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              {t("memory.drawerTitle")}
            </h2>
            {totalRecords > 0 && (
              <span className="px-1.5 py-0.5 text-[10px] font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-full">
                {totalRecords}
              </span>
            )}
          </div>
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
          {bucket?.loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400 dark:text-gray-500" />
              <span className="ml-2 text-sm text-gray-500 dark:text-gray-400">{t("memory.loading")}</span>
            </div>
          ) : (
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
              disabled={totalRecords === 0}
              className="w-full px-3 py-2 text-xs font-medium text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t("memory.forgetAll")}
            </button>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-600 dark:text-gray-400">{t("memory.forgetAllConfirm")}</p>
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
                    <Loader2 className="w-3.5 h-3.5 animate-spin mx-auto" />
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
