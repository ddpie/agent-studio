import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { Store, Search, Loader2, Bot, Package, Wrench, Copy, Tag } from "lucide-react";
import {
  fetchPublicAgents,
  fetchPublicSkills,
  fetchPublicTools,
  clonePublicAgent,
  clonePublicSkill,
  clonePublicTool,
  type PublicAgentItem,
  type PublicSkillItem,
  type PublicToolItem,
} from "../../lib/api-client";
import { toast } from "../../lib/toast";
import { useWorkspaceStore } from "../../stores/workspace-store";

type Tab = "agents" | "skills" | "tools";

const PAGE_SIZE = 24;

export default function MarketplacePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { currentWorkspace } = useWorkspaceStore();
  const role = currentWorkspace?.role || "viewer";
  const canClone = role === "editor" || role === "admin" || role === "owner";
  const disabledTooltip = canClone ? undefined : t("marketplace.cloneDisabledTooltip");
  const [tab, setTab] = useState<Tab>("agents");
  const [search, setSearch] = useState("");

  const [agents, setAgents] = useState<PublicAgentItem[]>([]);
  const [skills, setSkills] = useState<PublicSkillItem[]>([]);
  const [tools, setTools] = useState<PublicToolItem[]>([]);
  const [cursors, setCursors] = useState<Record<Tab, string | undefined>>({
    agents: undefined,
    skills: undefined,
    tools: undefined,
  });
  const [loaded, setLoaded] = useState<Record<Tab, boolean>>({
    agents: false,
    skills: false,
    tools: false,
  });
  const [loading, setLoading] = useState(false);
  const [cloning, setCloning] = useState<string | null>(null);

  const loadPage = useCallback(async (which: Tab, reset = false) => {
    setLoading(true);
    try {
      if (which === "agents") {
        const resp = await fetchPublicAgents(reset ? undefined : cursors.agents, PAGE_SIZE);
        setAgents((prev) => (reset ? resp.items : [...prev, ...resp.items]));
        setCursors((c) => ({ ...c, agents: resp.nextCursor }));
      } else if (which === "skills") {
        const resp = await fetchPublicSkills(reset ? undefined : cursors.skills, PAGE_SIZE);
        setSkills((prev) => (reset ? resp.items : [...prev, ...resp.items]));
        setCursors((c) => ({ ...c, skills: resp.nextCursor }));
      } else {
        const resp = await fetchPublicTools(reset ? undefined : cursors.tools, PAGE_SIZE);
        setTools((prev) => (reset ? resp.items : [...prev, ...resp.items]));
        setCursors((c) => ({ ...c, tools: resp.nextCursor }));
      }
      setLoaded((l) => ({ ...l, [which]: true }));
    } catch (err) {
      toast.error((err as Error).message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [cursors]);

  useEffect(() => {
    if (!loaded[tab]) void loadPage(tab, true);
  }, [tab, loaded, loadPage]);

  const handleCloneAgent = async (id: string, name: string) => {
    setCloning(`agent-${id}`);
    try {
      const res = await clonePublicAgent(id);
      toast.success(t("marketplace.cloneSuccess", { name }));
      navigate(`/agents/edit/${res.agentId}`);
    } catch (err) {
      toast.error((err as Error).message || "Clone failed");
    } finally {
      setCloning(null);
    }
  };

  const handleCloneSkill = async (id: string, name: string) => {
    setCloning(`skill-${id}`);
    try {
      const res = await clonePublicSkill(id);
      toast.success(t("marketplace.cloneSuccess", { name }));
      navigate(`/skills/${res.skillId}`);
    } catch (err) {
      toast.error((err as Error).message || "Clone failed");
    } finally {
      setCloning(null);
    }
  };

  const handleCloneTool = async (id: string, name: string) => {
    setCloning(`tool-${id}`);
    try {
      const res = await clonePublicTool(id);
      toast.success(t("marketplace.cloneSuccess", { name }));
      navigate(`/tools/${res.toolId}`);
    } catch (err) {
      toast.error((err as Error).message || "Clone failed");
    } finally {
      setCloning(null);
    }
  };

  const filteredAgents = agents.filter((a) =>
    !search ||
    a.name.toLowerCase().includes(search.toLowerCase()) ||
    (a.description || "").toLowerCase().includes(search.toLowerCase())
  );
  const filteredSkills = skills.filter((s) =>
    !search ||
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    (s.description || "").toLowerCase().includes(search.toLowerCase()) ||
    (s.tags || []).some((tag) => tag.toLowerCase().includes(search.toLowerCase()))
  );
  const filteredTools = tools.filter((t) =>
    !search ||
    t.name.toLowerCase().includes(search.toLowerCase()) ||
    (t.description || "").toLowerCase().includes(search.toLowerCase())
  );

  const hasMore = !!cursors[tab];

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Store className="w-4 h-4 text-blue-500" />
            {t("marketplace.title")}
          </h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("marketplace.subtitle")}</p>
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("marketplace.searchPlaceholder")}
            className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-56 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder:text-gray-400"
          />
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-6 pt-3 border-b border-gray-200 dark:border-gray-700">
        {(["agents", "skills", "tools"] as const).map((k) => {
          const Icon = k === "agents" ? Bot : k === "skills" ? Package : Wrench;
          const active = tab === k;
          return (
            <button
              key={k}
              onClick={() => setTab(k)}
              data-testid={`marketplace-tab-${k}`}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-t-lg transition-colors border-b-2 ${
                active
                  ? "border-blue-500 text-blue-600 dark:text-blue-400"
                  : "border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t(`marketplace.tab.${k}`)}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        {loading && !loaded[tab] ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : (
          <>
            {tab === "agents" && (
              <GridOrEmpty
                empty={filteredAgents.length === 0}
                emptyLabel={search ? t("marketplace.noMatching") : t("marketplace.emptyAgents")}
              >
                {filteredAgents.map((a) => (
                  <MarketCard
                    key={a.agentId}
                    icon={<Bot className="w-4 h-4 text-blue-500" />}
                    title={a.name}
                    description={a.description}
                    meta={a.model_id}
                    actionLabel={t("marketplace.cloneAgent")}
                    actionLoading={cloning === `agent-${a.agentId}`}
                    actionDisabled={!canClone}
                    actionTitle={disabledTooltip}
                    onAction={() => handleCloneAgent(a.agentId, a.name)}
                  />
                ))}
              </GridOrEmpty>
            )}
            {tab === "skills" && (
              <GridOrEmpty
                empty={filteredSkills.length === 0}
                emptyLabel={search ? t("marketplace.noMatching") : t("marketplace.emptySkills")}
              >
                {filteredSkills.map((s) => (
                  <MarketCard
                    key={s.skillId}
                    icon={<Package className="w-4 h-4 text-blue-500" />}
                    title={s.name}
                    description={s.description}
                    meta={s.type}
                    tags={s.tags}
                    actionLabel={t("marketplace.cloneSkill")}
                    actionLoading={cloning === `skill-${s.skillId}`}
                    actionDisabled={!canClone}
                    actionTitle={disabledTooltip}
                    onAction={() => handleCloneSkill(s.skillId, s.name)}
                  />
                ))}
              </GridOrEmpty>
            )}
            {tab === "tools" && (
              <GridOrEmpty
                empty={filteredTools.length === 0}
                emptyLabel={search ? t("marketplace.noMatching") : t("marketplace.emptyTools")}
              >
                {filteredTools.map((tool) => (
                  <MarketCard
                    key={tool.toolId}
                    icon={<Wrench className="w-4 h-4 text-blue-500" />}
                    title={tool.name}
                    description={tool.description}
                    meta={tool.category}
                    actionLabel={t("marketplace.cloneTool")}
                    actionLoading={cloning === `tool-${tool.toolId}`}
                    actionDisabled={!canClone}
                    actionTitle={disabledTooltip}
                    onAction={() => handleCloneTool(tool.toolId, tool.name)}
                  />
                ))}
              </GridOrEmpty>
            )}

            {hasMore && (
              <div className="flex justify-center mt-6">
                <button
                  onClick={() => void loadPage(tab, false)}
                  disabled={loading}
                  className="px-4 py-1.5 text-xs text-gray-600 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
                >
                  {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : t("marketplace.loadMore")}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function GridOrEmpty({
  empty,
  emptyLabel,
  children,
}: {
  empty: boolean;
  emptyLabel: string;
  children: React.ReactNode;
}) {
  if (empty) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400">
        <Store className="w-10 h-10 mb-3 opacity-30" />
        <p className="text-sm font-medium text-gray-600 dark:text-gray-400">{emptyLabel}</p>
      </div>
    );
  }
  return <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">{children}</div>;
}

function MarketCard({
  icon,
  title,
  description,
  meta,
  tags,
  actionLabel,
  actionLoading,
  actionDisabled,
  actionTitle,
  onAction,
}: {
  icon: React.ReactNode;
  title: string;
  description?: string;
  meta?: string;
  tags?: string[];
  actionLabel: string;
  actionLoading: boolean;
  actionDisabled?: boolean;
  actionTitle?: string;
  onAction: () => void;
}) {
  const disabled = actionLoading || !!actionDisabled;
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 flex flex-col gap-2 hover:border-blue-300 dark:hover:border-blue-600 hover:shadow-sm transition-all">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 flex-1 truncate">{title}</h3>
      </div>
      {description && (
        <p className="text-xs text-gray-500 dark:text-gray-400 line-clamp-2 min-h-[2em]">{description}</p>
      )}
      <div className="flex items-center gap-2 text-[10px] text-gray-400 min-h-[16px]">
        {meta && <span className="font-mono truncate">{meta}</span>}
        {tags && tags.length > 0 && (
          <span className="flex items-center gap-0.5">
            <Tag className="w-2.5 h-2.5" />
            {tags.slice(0, 3).join(", ")}
          </span>
        )}
      </div>
      <button
        onClick={onAction}
        disabled={disabled}
        title={actionTitle}
        aria-disabled={disabled}
        className="mt-1 flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {actionLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Copy className="w-3.5 h-3.5" />}
        {actionLabel}
      </button>
    </div>
  );
}
