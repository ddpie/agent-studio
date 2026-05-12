import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Bot, ExternalLink } from "lucide-react";

interface Props {
  agentIds: string[];
  kbName: string;
}

export default function KBAttachedAgents({ agentIds, kbName }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">
          {t("kb.attachedAgents")}
        </h4>
        <button
          onClick={() =>
            navigate(
              `/agents?prefill=${encodeURIComponent(`Attach knowledge base "${kbName}" to `)}`
            )
          }
          className="flex items-center gap-1 px-2 py-1 text-[11px] text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg"
        >
          <ExternalLink className="w-3 h-3" />
          {t("kb.attachHint")}
        </button>
      </div>

      {agentIds.length === 0 ? (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          No attached agents
        </p>
      ) : (
        <div className="space-y-1">
          {agentIds.map((id) => (
            <button
              key={id}
              onClick={() => navigate(`/agents/chat/${id}`)}
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 w-full text-left transition-colors"
            >
              <Bot className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
              <span className="text-xs text-gray-700 dark:text-gray-300 font-mono truncate">
                {id}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
