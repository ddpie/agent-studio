/**
 * KnowledgeBaseSection — Attach/detach knowledge bases to an agent for RAG.
 *
 * Uses direct REST endpoints (attachKnowledgeBase / detachKnowledgeBase) rather
 * than Meta-Agent streaming. KB list is fetched from the kb-store.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Database, ExternalLink, Loader2, Plus, X } from "lucide-react";
import { useKBStore } from "../../stores/kb-store";
import { useWorkspaceStore } from "../../stores/workspace-store";
import { attachKnowledgeBase, detachKnowledgeBase } from "../../lib/api-client";
import Section from "./shared/Section";

const inputClass = "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";

const MAX_KBS = 5;

interface Props {
  agentId: string;
  knowledgeBases: string[];
  onChange: (kbs: string[]) => void;
}

export default function KnowledgeBaseSection({ agentId, knowledgeBases, onChange }: Props) {
  const { t } = useTranslation();
  const { items: kbItems, loading: kbLoading, fetchList } = useKBStore();
  const { currentWorkspace } = useWorkspaceStore();
  const role = currentWorkspace?.role || "viewer";
  const canEdit = role === "editor" || role === "admin" || role === "owner";

  const [selected, setSelected] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!kbItems.length && !kbLoading) fetchList();
  }, [kbItems.length, kbLoading, fetchList]);

  const attachedSet = useMemo(() => new Set(knowledgeBases), [knowledgeBases]);
  const candidates = useMemo(
    () => kbItems.filter((kb) => !attachedSet.has(kb.kbId)),
    [kbItems, attachedSet],
  );

  const attachedDetails = useMemo(
    () =>
      knowledgeBases.map((kbId) => {
        const item = kbItems.find((kb) => kb.kbId === kbId);
        return {
          kbId,
          name: item?.name || kbId,
          docCount: item?.docCount ?? 0,
          status: item?.status || "unknown",
        };
      }),
    [knowledgeBases, kbItems],
  );

  const handleAttach = async () => {
    if (!selected || !canEdit) return;
    if (knowledgeBases.length >= MAX_KBS) {
      setError(t("knowledgeBases.maxReached"));
      return;
    }
    setBusy(selected);
    setError(null);
    try {
      await attachKnowledgeBase(agentId, selected);
      onChange([...knowledgeBases, selected]);
      setSelected("");
    } catch (e) {
      setError(t("knowledgeBases.attachFailed"));
    } finally {
      setBusy(null);
    }
  };

  const handleDetach = async (kbId: string) => {
    if (!canEdit) return;
    setBusy(kbId);
    setError(null);
    try {
      await detachKnowledgeBase(agentId, kbId);
      onChange(knowledgeBases.filter((id) => id !== kbId));
    } catch (e) {
      setError(t("knowledgeBases.detachFailed"));
    } finally {
      setBusy(null);
    }
  };

  const statusBadge = (status: string) => {
    const colors =
      status === "ACTIVE" || status === "active"
        ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
        : status === "CREATING" || status === "creating"
          ? "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400"
          : "bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400";
    return (
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${colors}`}>
        {status}
      </span>
    );
  };

  return (
    <Section title={t("knowledgeBases.title")} icon={<Database className="w-3.5 h-3.5" />}>
      <p className="text-[11px] text-gray-400 dark:text-gray-500">
        {t("knowledgeBases.description")}
      </p>

      {error && (
        <div className="px-3 py-2 rounded-lg text-xs bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800">
          {error}
        </div>
      )}

      {attachedDetails.length > 0 ? (
        <ul className="space-y-1">
          {attachedDetails.map((kb) => (
            <li
              key={kb.kbId}
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-100 dark:border-gray-700"
            >
              <Database className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium text-gray-800 dark:text-gray-100 truncate">
                    {kb.name}
                  </span>
                  {statusBadge(kb.status)}
                </div>
                <div className="text-[11px] text-gray-400 dark:text-gray-500">
                  {kb.docCount} {t("knowledgeBases.docs")}
                </div>
              </div>
              <a
                href={`#/knowledge-bases/${kb.kbId}`}
                className="p-1.5 text-gray-300 dark:text-gray-600 hover:text-blue-500 dark:hover:text-blue-400 transition-colors"
                title={t("knowledgeBases.viewDetail")}
              >
                <ExternalLink className="w-3.5 h-3.5" />
              </a>
              <button
                onClick={() => handleDetach(kb.kbId)}
                disabled={busy === kb.kbId || !canEdit}
                className="p-1.5 text-gray-300 dark:text-gray-600 hover:text-red-500 dark:hover:text-red-400 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:text-gray-300 dark:disabled:hover:text-gray-600"
                title={canEdit ? t("knowledgeBases.detach") : t("knowledgeBases.editorRequired")}
              >
                {busy === kb.kbId ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <X className="w-3.5 h-3.5" />
                )}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[12px] text-gray-400 dark:text-gray-500 italic">
          {t("knowledgeBases.empty")}
        </p>
      )}

      <div className="flex gap-2 items-center pt-1">
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className={inputClass + " flex-1 disabled:opacity-50 disabled:cursor-not-allowed"}
          disabled={busy !== null || candidates.length === 0 || !canEdit || knowledgeBases.length >= MAX_KBS}
          title={canEdit ? undefined : t("knowledgeBases.editorRequired")}
        >
          <option value="">
            {kbLoading
              ? t("common.loading")
              : candidates.length === 0
                ? t("knowledgeBases.noAvailable")
                : knowledgeBases.length >= MAX_KBS
                  ? t("knowledgeBases.maxReached")
                  : t("knowledgeBases.selectKb")}
          </option>
          {candidates.map((kb) => (
            <option key={kb.kbId} value={kb.kbId}>
              {kb.name} ({kb.docCount} {t("knowledgeBases.docs")})
            </option>
          ))}
        </select>
        <button
          onClick={handleAttach}
          disabled={!selected || busy !== null || !canEdit || knowledgeBases.length >= MAX_KBS}
          title={canEdit ? undefined : t("knowledgeBases.editorRequired")}
          className="flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy === selected ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Plus className="w-3.5 h-3.5" />
          )}
          {busy === selected ? t("knowledgeBases.attaching") : t("knowledgeBases.attach")}
        </button>
      </div>
      {!canEdit && (
        <p className="text-[11px] text-gray-400 dark:text-gray-500 italic">
          {t("knowledgeBases.editorRequired")}
        </p>
      )}
    </Section>
  );
}
