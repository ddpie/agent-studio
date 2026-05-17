import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  MessageSquare,
  Plug,
  Pause,
  Play,
  Settings,
  Trash2,
  GitBranch,
  Plus,
} from "lucide-react";
import { useChannelStore, type Channel } from "../../stores/channel-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { toast } from "../../lib/toast";
import ConfirmDialog from "../ui/ConfirmDialog";
import ChannelWizard from "./ChannelWizard";
import ChannelRouting from "./ChannelRouting";

function StatusBadge({ status }: { status: Channel["status"] }) {
  const { t } = useTranslation();
  const colors: Record<Channel["status"], string> = {
    active: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300",
    paused: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300",
    error: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
    provisioning: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  };
  const dots: Record<Channel["status"], string> = {
    active: "bg-green-500",
    paused: "bg-yellow-500",
    error: "bg-red-500",
    provisioning: "bg-blue-500",
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${colors[status]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dots[status]}`} />
      {t(`channels.status_${status}`)}
    </span>
  );
}

function TypeBadge({ type }: { type: Channel["channelType"] }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
      {t(`channels.${type}`)}
    </span>
  );
}

export default function ChannelsTab() {
  const { t } = useTranslation();
  const { channels, loading, error, fetchChannels, updateChannel, deleteChannel } =
    useChannelStore();
  const { agents, fetchAgents } = useAgentListStore();

  const [wizardOpen, setWizardOpen] = useState(false);
  const [routingChannel, setRoutingChannel] = useState<Channel | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null);

  useEffect(() => {
    fetchChannels();
    fetchAgents();
  }, [fetchChannels, fetchAgents]);

  const agentNameById = (id: string): string => {
    const a = agents.find((ag) => ag.id === id);
    return a?.displayName || a?.name || id;
  };

  const handlePauseResume = async (ch: Channel) => {
    try {
      const newStatus = ch.status === "active" ? "paused" : "active";
      await updateChannel(ch.channelId, { status: newStatus });
      toast.success(
        newStatus === "paused"
          ? t("channels.pause") + " OK"
          : t("channels.resume") + " OK"
      );
    } catch {
      toast.error(t("common.error"));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteChannel(deleteTarget.channelId);
      toast.success(t("channels.delete") + " OK");
    } catch {
      toast.error(t("common.error"));
    }
    setDeleteTarget(null);
  };

  const formatTime = (iso?: string) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  };

  if (wizardOpen) {
    return (
      <ChannelWizard
        onClose={() => setWizardOpen(false)}
        onCreated={() => {
          setWizardOpen(false);
          fetchChannels();
        }}
      />
    );
  }

  if (routingChannel) {
    return (
      <ChannelRouting
        channel={routingChannel}
        onClose={() => setRoutingChannel(null)}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
          <MessageSquare className="w-3.5 h-3.5" />
          {t("channels.title")}
        </h3>
        <button
          onClick={() => setWizardOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
        >
          <Plus className="w-3.5 h-3.5" />
          {t("channels.connect")}
        </button>
      </div>

      {loading && (
        <div className="text-xs text-gray-500 dark:text-gray-400 py-4">
          {t("common.loading")}
        </div>
      )}

      {error && !loading && (
        <div className="text-xs text-red-600 dark:text-red-400 py-2">{error}</div>
      )}

      {!loading && !error && channels.length === 0 && (
        <div className="border border-dashed border-gray-300 dark:border-gray-700 rounded-lg p-8 text-center">
          <Plug className="w-8 h-8 mx-auto text-gray-400 dark:text-gray-500 mb-3" />
          <p className="text-xs text-gray-500 dark:text-gray-400 max-w-sm mx-auto">
            {t("channels.empty")}
          </p>
          <button
            onClick={() => setWizardOpen(true)}
            className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
          >
            <Plus className="w-3.5 h-3.5" />
            {t("channels.connect")}
          </button>
        </div>
      )}

      {!loading && channels.length > 0 && (
        <div className="space-y-2">
          {channels.map((ch) => (
            <div
              key={ch.channelId}
              className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 flex items-center gap-3"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">
                    {ch.channelName}
                  </span>
                  <TypeBadge type={ch.channelType} />
                  <StatusBadge status={ch.status} />
                </div>
                <div className="flex items-center gap-3 text-[10px] text-gray-500 dark:text-gray-400">
                  <span>{t(`channels.trigger_${ch.triggerMode}`)}</span>
                  <span>
                    {t("channels.default_agent")}: {agentNameById(ch.defaultAgentId)}
                  </span>
                  {ch.lastMessageAt && (
                    <span>
                      {t("channels.last_message")}: {formatTime(ch.lastMessageAt)}
                    </span>
                  )}
                  {ch.messageCount != null && (
                    <span>
                      {ch.messageCount} {t("channels.messages_count")}
                    </span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => handlePauseResume(ch)}
                  disabled={ch.status === "provisioning" || ch.status === "error"}
                  title={ch.status === "active" ? t("channels.pause") : t("channels.resume")}
                  className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 disabled:opacity-40"
                >
                  {ch.status === "active" ? (
                    <Pause className="w-3.5 h-3.5" />
                  ) : (
                    <Play className="w-3.5 h-3.5" />
                  )}
                </button>
                <button
                  onClick={() => setRoutingChannel(ch)}
                  title={t("channels.routing")}
                  className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400"
                >
                  <GitBranch className="w-3.5 h-3.5" />
                </button>
                <button
                  title={t("common.edit")}
                  className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400"
                >
                  <Settings className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => setDeleteTarget(ch)}
                  title={t("channels.delete")}
                  className="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-red-500 dark:text-red-400"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("channels.delete")}
        message={t("channels.delete_confirm")}
        confirmLabel={t("channels.delete")}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
        danger
      />
    </div>
  );
}
