import { useTranslation } from "react-i18next";
import { formatDateTime } from "../../lib/date-format";
import type { KBDetailResponse } from "../../lib/api-client";

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    ACTIVE: "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400",
    CREATING: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
    FAILED: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400",
    DELETING: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
  };
  const cls = colors[status] || colors.ACTIVE;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${cls}`}>
      {status}
    </span>
  );
}

interface Props {
  kb: KBDetailResponse;
}

export default function KBInfoCard({ kb }: Props) {
  const { t } = useTranslation();

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
      <div className="flex items-center gap-3 mb-3">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
          {kb.name}
        </h3>
        <StatusBadge status={kb.status} />
      </div>
      {kb.description && (
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
          {kb.description}
        </p>
      )}
      <div className="grid grid-cols-2 gap-y-2 gap-x-6 text-xs">
        <div>
          <span className="text-gray-500 dark:text-gray-400">ID:</span>
          <span className="ml-2 font-mono text-gray-700 dark:text-gray-300">
            {kb.kbId}
          </span>
        </div>
        <div>
          <span className="text-gray-500 dark:text-gray-400">Bedrock KB ID:</span>
          <span className="ml-2 font-mono text-gray-700 dark:text-gray-300 text-[11px]">
            {kb.bedrockKbId}
          </span>
        </div>
        <div>
          <span className="text-gray-500 dark:text-gray-400">
            Embedding Model:
          </span>
          <span className="ml-2 text-gray-700 dark:text-gray-300">
            {kb.embeddingModel || "—"}
          </span>
        </div>
        <div>
          <span className="text-gray-500 dark:text-gray-400">
            {t("kb.docCount")}:
          </span>
          <span className="ml-2 text-gray-700 dark:text-gray-300">
            {kb.docCount}
          </span>
        </div>
        <div>
          <span className="text-gray-500 dark:text-gray-400">Created:</span>
          <span className="ml-2 text-gray-700 dark:text-gray-300">
            {formatDateTime(kb.createdAt)}
          </span>
        </div>
        <div>
          <span className="text-gray-500 dark:text-gray-400">Created by:</span>
          <span className="ml-2 text-gray-700 dark:text-gray-300">
            {kb.createdBy || "—"}
          </span>
        </div>
      </div>
    </div>
  );
}
