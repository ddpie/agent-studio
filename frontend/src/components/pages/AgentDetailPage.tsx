import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { Pencil, Loader2 } from "lucide-react";
import { fetchAgent } from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import DeploymentsTab from "../agents/DeploymentsTab";
import EndpointsTab from "../agents/EndpointsTab";
import EvaluationsTab from "../agents/EvaluationsTab";
import TracesTab from "../agents/TracesTab";
import IntegrationTab from "../agents/IntegrationTab";

export default function AgentDetailPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  const [agent, setAgent] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    if (!agentId) return;
    fetchAgent(agentId)
      .then((data) => setAgent(data as Record<string, unknown>))
      .catch((err) => setError(err as Error));
  }, [agentId]);

  if (error) {
    return (
      <div className="flex flex-col h-full">
        <div
          className="flex-1 flex items-center justify-center text-red-600 text-sm"
          data-testid="agent-detail-error"
        >
          {t("agentDetail.loadError")}: {error.message}
        </div>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  const role = currentWorkspace?.role || "viewer";
  const canEdit = role === "editor" || role === "admin" || role === "owner";
  const agentName =
    String(agent.display_name ?? "") ||
    String(agent.name ?? "") ||
    String(agentId ?? "");

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2
            className="text-base font-semibold text-gray-900 dark:text-gray-100"
            data-testid="agent-detail-title"
          >
            {t("agentDetail.title", { name: agentName })}
          </h2>
          <p
            className="text-xs text-gray-500 dark:text-gray-400"
            data-testid="agent-detail-subtitle"
          >
            {t("agentDetail.subtitle")}
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            onClick={() => navigate(`/agents/edit/${agentId}`)}
            data-testid="edit-agent-btn"
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600"
          >
            <Pencil className="w-3.5 h-3.5" />
            {t("agentDetail.editAgent")}
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-6" data-testid="agent-detail-content">
        {agentId && (
          <section className="mb-8" data-testid="deployments-section">
            <DeploymentsTab agentId={agentId} />
          </section>
        )}
        {agentId && (
          <section className="mb-8" data-testid="endpoints-section">
            <EndpointsTab agentId={agentId} />
          </section>
        )}
        {agentId && (
          <section className="mb-8" data-testid="evaluations-section">
            <EvaluationsTab agentId={agentId} />
          </section>
        )}
        {agentId && (
          <section className="mb-8" data-testid="traces-section">
            <TracesTab agentId={agentId} />
          </section>
        )}
        {agentId && (
          <section className="mb-8" data-testid="integration-section">
            <IntegrationTab agentId={agentId} />
          </section>
        )}
      </div>
    </div>
  );
}
