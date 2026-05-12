import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { ChevronLeft, RefreshCw, Loader2, Trash2, Database } from "lucide-react";
import { useKBStore } from "../../stores/kb-store";
import KBInfoCard from "./KBInfoCard";
import KBDocumentUpload from "./KBDocumentUpload";
import KBDocumentTable from "./KBDocumentTable";
import KBIngestionStatus from "./KBIngestionStatus";
import KBAttachedAgents from "./KBAttachedAgents";

export default function KBDetail() {
  const { kbId } = useParams<{ kbId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    currentDetail,
    detailLoading,
    detailError,
    fetchDetail,
    deleteKB,
    uploadDocument,
    deleteDocument,
    checkIngestion,
  } = useKBStore();

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (kbId) fetchDetail(kbId);
  }, [kbId, fetchDetail]);

  const refresh = useCallback(() => {
    if (kbId) fetchDetail(kbId);
  }, [kbId, fetchDetail]);

  const handleDelete = async () => {
    if (!kbId) return;
    setDeleting(true);
    try {
      await deleteKB(kbId);
      navigate("/knowledge-bases");
    } catch {
      setDeleting(false);
    }
  };

  const handleUpload = useCallback(
    async (stagingKey: string, filename: string) => {
      if (!kbId) return;
      await uploadDocument(kbId, stagingKey, filename);
    },
    [kbId, uploadDocument]
  );

  const handleDeleteDoc = useCallback(
    async (documentKey: string) => {
      if (!kbId) return;
      await deleteDocument(kbId, documentKey);
    },
    [kbId, deleteDocument]
  );

  const handleIngestionRefresh = useCallback(async () => {
    if (!kbId) return;
    await checkIngestion(kbId);
  }, [kbId, checkIngestion]);

  if (detailLoading && !currentDetail) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500" />
      </div>
    );
  }

  if (detailError) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500">
        <Database className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
          {t("kb.notFound")}
        </p>
        <button
          onClick={() => navigate("/knowledge-bases")}
          className="mt-3 text-xs text-blue-500 hover:text-blue-600"
        >
          {t("common.back")}
        </button>
      </div>
    );
  }

  if (!currentDetail) return null;

  const kb = currentDetail;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <button
            onClick={() => navigate("/knowledge-bases")}
            className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded"
            title={t("common.back")}
          >
            <ChevronLeft className="w-4 h-4 text-gray-500 dark:text-gray-400" />
          </button>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {kb.name}
          </h2>
        </div>
        <button
          onClick={refresh}
          className="p-1.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
          title={t("kb.refresh")}
        >
          {detailLoading ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        <KBInfoCard kb={kb} />

        {/* Upload section */}
        <div>
          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">
            {t("kb.upload")}
          </h4>
          <KBDocumentUpload kbId={kb.kbId} onUploaded={handleUpload} />
        </div>

        {/* Ingestion status */}
        {kb.ingestion && <KBIngestionStatus ingestion={kb.ingestion} onRefresh={handleIngestionRefresh} />}

        {/* Documents table */}
        <div>
          <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-2">
            {t("kb.docCount")} ({kb.documents.length})
          </h4>
          <KBDocumentTable documents={kb.documents} onDelete={handleDeleteDoc} />
        </div>

        {/* Attached agents */}
        <KBAttachedAgents agentIds={kb.attachedAgentIds} kbName={kb.name} />

        {/* Delete section */}
        <div className="border border-red-200 dark:border-red-900/50 rounded-lg p-4">
          <h4 className="text-sm font-medium text-red-600 dark:text-red-400 mb-1">
            {t("kb.delete")}
          </h4>
          {!confirmDelete ? (
            <button
              onClick={() => setConfirmDelete(true)}
              className="flex items-center gap-1 px-3 py-1.5 text-xs text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {t("kb.delete")}
            </button>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-red-600 dark:text-red-400">
                {t("kb.deleteConfirm", { name: kb.name })}
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600 disabled:opacity-50"
                >
                  {deleting ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    t("common.confirm")
                  )}
                </button>
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg"
                >
                  {t("common.cancel")}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
