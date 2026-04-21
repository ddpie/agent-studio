import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Pencil,
  Loader2,
  Rocket,
  ScrollText,
  Network,
  KeyRound,
  ClipboardCheck,
  Activity,
  DollarSign,
  Share2,
  Clock,
} from "lucide-react";
import { fetchAgent, publishAgent, unpublishAgent } from "../../lib/api-client";
import { useWorkspaceStore } from "../../stores/workspace-store";
import DeploymentsTab from "../agents/DeploymentsTab";
import LogsTab from "../agents/LogsTab";
import EndpointsTab from "../agents/EndpointsTab";
import SecretsTab from "../agents/SecretsTab";
import EvaluationsTab from "../agents/EvaluationsTab";
import RunsTab from "../agents/RunsTab";
import AgentCostsSection from "../agents/AgentCostsSection";
import IntegrationTab from "../agents/IntegrationTab";
import SchedulesTab from "../agents/SchedulesTab";
import PublishToggle from "../shared/PublishToggle";
import DetailSideNav, { type NavEntry } from "../agents/DetailSideNav";
import LazySection from "../agents/LazySection";
import { useScrollSpy } from "../../hooks/useScrollSpy";

const RUNS_SECTION_ID = "runs-section";

export default function AgentDetailPage() {
  const { agentId, runId: routeRunId } = useParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspaceStore();
  const [agent, setAgent] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(routeRunId ?? null);
  const scrollRootRef = useRef<HTMLDivElement | null>(null);

  const navItems: NavEntry[] = useMemo(
    () => [
      { id: RUNS_SECTION_ID, label: t("runs.tab"), icon: <Activity className="w-3.5 h-3.5" /> },
      { id: "schedules-section", label: t("schedules.title"), icon: <Clock className="w-3.5 h-3.5" /> },
      { id: "evaluations-section", label: t("evaluations.tab"), icon: <ClipboardCheck className="w-3.5 h-3.5" /> },
      { id: "costs-section", label: t("costs.title"), icon: <DollarSign className="w-3.5 h-3.5" /> },
      { id: "integration-section", label: t("integration.title"), icon: <Share2 className="w-3.5 h-3.5" /> },
      {
        type: "group",
        id: "advanced",
        label: t("agentDetail.sideNav.advanced"),
        items: [
          { id: "deployments-section", label: t("deployments.tab"), icon: <Rocket className="w-3.5 h-3.5" /> },
          { id: "endpoints-section", label: t("endpoints.tab"), icon: <Network className="w-3.5 h-3.5" /> },
          { id: "secrets-section", label: t("secrets.title"), icon: <KeyRound className="w-3.5 h-3.5" /> },
          { id: "logs-section", label: t("logs.sectionTitle"), icon: <ScrollText className="w-3.5 h-3.5" /> },
        ],
      },
    ],
    [t],
  );

  const flatIds = useMemo(() => {
    const ids: string[] = [];
    for (const entry of navItems) {
      if ("type" in entry && entry.type === "group") {
        for (const c of entry.items) ids.push(c.id);
      } else {
        ids.push((entry as { id: string }).id);
      }
    }
    return ids;
  }, [navItems]);

  const { activeId, suppressFor, setActiveId } = useScrollSpy(flatIds, scrollRootRef);

  const handleNavNavigate = (id: string) => {
    // Lock scroll-spy for ~700ms while the smooth scroll plays out, and
    // optimistically pin the active highlight to the clicked item so the
    // nav never strobes through intermediate sections.
    suppressFor(800);
    setActiveId(id);
  };

  const handleRunSelected = (runId: string) => {
    if (!agentId) return;
    if (runId === routeRunId) return;
    navigate(`/agents/${agentId}/runs/${encodeURIComponent(runId)}`);
  };

  const scrollToRuns = (runId: string) => {
    setSelectedRunId(runId);
    handleRunSelected(runId);
  };

  useEffect(() => {
    if (!agentId) return;
    fetchAgent(agentId)
      .then((data) => setAgent(data as Record<string, unknown>))
      .catch((err) => setError(err as Error));
  }, [agentId]);

  useEffect(() => {
    if (routeRunId) setSelectedRunId(routeRunId);
  }, [routeRunId]);

  if (error) {
    return (
      <div className="flex flex-col h-full">
        <div
          className="flex-1 flex items-center justify-center text-red-600 dark:text-red-400 text-sm"
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
          <Loader2 className="w-6 h-6 animate-spin text-gray-400 dark:text-gray-500" />
        </div>
      </div>
    );
  }

  const role = currentWorkspace?.role || "viewer";
  const canEdit = role === "editor" || role === "admin" || role === "owner";
  const canPublish = role === "admin" || role === "owner";
  const agentName =
    String(agent.display_name ?? "") ||
    String(agent.name ?? "") ||
    String(agentId ?? "");
  const visibility = String(agent.visibility ?? "private");

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
        <div className="flex items-center gap-2">
          {agentId && (
            <PublishToggle
              visibility={visibility}
              canPublish={canPublish}
              onPublish={async () => { await publishAgent(agentId); }}
              onUnpublish={async () => { await unpublishAgent(agentId); }}
              onChange={(v) => setAgent((prev) => (prev ? { ...prev, visibility: v } : prev))}
              testId="agent-publish-toggle"
              size="md"
            />
          )}
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
      </div>

      <div className="flex-1 min-h-0 flex">
        <div
          ref={scrollRootRef}
          className="flex-1 overflow-y-auto"
          data-testid="agent-detail-content"
        >
          <div className="flex gap-2 p-6 max-w-[1600px] mx-auto">
            <DetailSideNav
              items={navItems}
              activeId={activeId}
              scrollRootRef={scrollRootRef}
              memoryKey={agentId}
              onNavigate={handleNavNavigate}
            />
            <div className="flex-1 min-w-0 space-y-8">
              {agentId && (
                <LazySection
                  id={RUNS_SECTION_ID}
                  testId="runs-section"
                  rootRef={scrollRootRef}
                  minHeight={400}
                  // Eager so deep-links and "View trace" from Schedules
                  // land on real content (not a placeholder that hydrates
                  // after the jump and shifts the scroll target).
                  eager
                >
                  <RunsTab
                    agentId={agentId}
                    initialRunId={selectedRunId}
                    onSelect={handleRunSelected}
                  />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="schedules-section"
                  testId="schedules-section"
                  rootRef={scrollRootRef}
                >
                  <SchedulesTab agentId={agentId} onViewTrace={scrollToRuns} />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="evaluations-section"
                  testId="evaluations-section"
                  rootRef={scrollRootRef}
                >
                  <EvaluationsTab agentId={agentId} />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="costs-section"
                  testId="costs-section"
                  rootRef={scrollRootRef}
                >
                  <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/30">
                    <AgentCostsSection agentId={agentId} />
                  </div>
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="integration-section"
                  testId="integration-section"
                  rootRef={scrollRootRef}
                >
                  <IntegrationTab agentId={agentId} />
                </LazySection>
              )}
              {/* Advanced group — rendered linearly in DOM so scroll-spy
                  tracks them even while the nav group is collapsed. */}
              {agentId && (
                <LazySection
                  id="deployments-section"
                  testId="deployments-section"
                  rootRef={scrollRootRef}
                >
                  <DeploymentsTab agentId={agentId} />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="endpoints-section"
                  testId="endpoints-section"
                  rootRef={scrollRootRef}
                >
                  <EndpointsTab agentId={agentId} />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="secrets-section"
                  testId="secrets-section"
                  rootRef={scrollRootRef}
                >
                  <SecretsTab agentId={agentId} />
                </LazySection>
              )}
              {agentId && (
                <LazySection
                  id="logs-section"
                  testId="logs-section"
                  rootRef={scrollRootRef}
                >
                  <LogsTab agentId={agentId} />
                </LazySection>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
