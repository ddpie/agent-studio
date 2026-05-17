import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, GitBranch, Plus, Trash2 } from "lucide-react";
import { useChannelStore, type Channel, type RoutingRule } from "../../stores/channel-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { toast } from "../../lib/toast";

interface ChannelRoutingProps {
  channel: Channel;
  onClose: () => void;
}

export default function ChannelRouting({ channel, onClose }: ChannelRoutingProps) {
  const { t } = useTranslation();
  const { updateChannel } = useChannelStore();
  const { agents } = useAgentListStore();

  const [defaultAgentId, setDefaultAgentId] = useState(channel.defaultAgentId);
  const [triggerMode, setTriggerMode] = useState(channel.triggerMode || "mention");
  const [rules, setRules] = useState<RoutingRule[]>(channel.routingRules || []);
  const [saving, setSaving] = useState(false);

  const addRule = () => {
    setRules([...rules, { type: "group", chatId: "", agentId: "" }]);
  };

  const removeRule = (index: number) => {
    setRules(rules.filter((_, i) => i !== index));
  };

  const updateRule = (index: number, patch: Partial<RoutingRule>) => {
    setRules(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateChannel(channel.channelId, {
        defaultAgentId,
        triggerMode,
        routingRules: rules.filter((r) => r.agentId),
      });
      toast.success(t("common.success"));
      onClose();
    } catch {
      toast.error(t("common.error"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide flex items-center gap-1.5">
          <GitBranch className="w-3.5 h-3.5" />
          {t("channels.routing")} — {channel.channelName}
        </h3>
      </div>

      {/* Trigger Mode */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
          {t("channels.trigger_mode_label") || "Trigger Mode"}
        </label>
        <div className="flex gap-3 mt-1">
          {(["mention", "all", "keyword"] as const).map((mode) => (
            <label key={mode} className="flex items-center gap-1.5 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
              <input
                type="radio"
                name="triggerMode"
                value={mode}
                checked={triggerMode === mode}
                onChange={() => setTriggerMode(mode)}
                className="accent-blue-500"
              />
              {t(`channels.trigger_${mode}`)}
            </label>
          ))}
        </div>
        {triggerMode === "all" && (
          <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1.5">
            ⚠️ {t("channels.trigger_all_hint") || "Requires 'im:message:group_msg' permission on Feishu. Add it in 权限管理 on the Feishu Open Platform."}
          </p>
        )}
      </section>

      {/* Default Agent */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <label className="block text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-1">
          {t("channels.default_agent")}
        </label>
        <p className="text-[10px] text-gray-400 dark:text-gray-500 mb-2">
          {t("channels.routing_default_desc")}
        </p>
        <select
          value={defaultAgentId}
          onChange={(e) => setDefaultAgentId(e.target.value)}
          className="w-full px-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
        >
          <option value="">—</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.displayName || a.name}
            </option>
          ))}
        </select>
      </section>

      {/* Group bindings */}
      <section className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <label className="text-[10px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
            {t("channels.routing_bindings")}
          </label>
          <button
            onClick={addRule}
            className="flex items-center gap-1 px-2 py-1 text-[10px] text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded"
          >
            <Plus className="w-3 h-3" />
            {t("channels.routing_add_binding")}
          </button>
        </div>

        {rules.length === 0 && (
          <p className="text-[11px] text-gray-400 dark:text-gray-500">
            {t("channels.routing_no_bindings")}
          </p>
        )}

        <div className="space-y-2">
          {rules.map((rule, i) => (
            <div
              key={i}
              className="flex items-center gap-2 p-2 bg-gray-50 dark:bg-gray-900 rounded-lg"
            >
              <input
                type="text"
                value={rule.chatId || ""}
                onChange={(e) => updateRule(i, { chatId: e.target.value })}
                placeholder={t("channels.routing_group_id")}
                className="flex-1 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200 focus:outline-none focus:border-blue-500"
              />
              <span className="text-[10px] text-gray-400 dark:text-gray-500">→</span>
              <select
                value={rule.agentId}
                onChange={(e) => updateRule(i, { agentId: e.target.value })}
                className="flex-1 px-2 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-950 text-gray-700 dark:text-gray-200"
              >
                <option value="">—</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.displayName || a.name}
                  </option>
                ))}
              </select>
              <button
                onClick={() => removeRule(i)}
                className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 text-red-500 dark:text-red-400"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <div className="flex justify-end gap-2">
        <button
          onClick={onClose}
          className="px-3 py-1.5 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
        >
          {t("common.cancel")}
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white rounded-lg disabled:opacity-50"
        >
          {saving ? t("common.loading") : t("common.save")}
        </button>
      </div>
    </div>
  );
}
