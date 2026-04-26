import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Brain, Loader2, Check } from "lucide-react";
import Section from "./shared/Section";
import { updateAgent } from "../../lib/api-client";
import { toast } from "../../lib/toast";

interface MemorySectionProps {
  agentId?: string;
  value: { enabled: boolean; strategies: string[] };
  onChange: (v: { enabled: boolean; strategies: string[] }) => void;
  workspaceMemoryAvailable: boolean;
}

export default function MemorySection({
  agentId,
  value,
  onChange,
  workspaceMemoryAvailable,
}: MemorySectionProps) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleToggle = (enabled: boolean) => {
    const next = { ...value, enabled };
    onChange(next);
    autoSave(next);
  };

  const handleStrategyToggle = (strategy: string) => {
    const strategies = value.strategies.includes(strategy)
      ? value.strategies.filter((s) => s !== strategy)
      : [...value.strategies, strategy];
    const next = { ...value, strategies };
    onChange(next);
    autoSave(next);
  };

  const autoSave = useCallback(
    async (mem: { enabled: boolean; strategies: string[] }) => {
      if (!agentId) return;
      setSaving(true);
      setSaved(false);
      try {
        await updateAgent(agentId, { memory: mem });
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Save failed";
        toast.error(msg);
      } finally {
        setSaving(false);
      }
    },
    [agentId],
  );

  const strategies = [
    { key: "userPreference", label: t("memory.builder.strategyPreferences"), hint: t("memory.section.preferencesHint") },
    { key: "semantic", label: t("memory.builder.strategyFacts"), hint: t("memory.section.factsHint") },
    { key: "summary", label: t("memory.builder.strategySummaries"), hint: t("memory.section.summariesHint") },
    { key: "episodic", label: t("memory.builder.strategyEpisodes"), hint: t("memory.section.episodesHint") },
  ];

  return (
    <Section
      title={t("memory.builder.title")}
      icon={<Brain className="w-3.5 h-3.5" />}
      action={
        saving ? (
          <Loader2 className="w-3 h-3 animate-spin text-blue-500" />
        ) : saved ? (
          <Check className="w-3 h-3 text-green-500" />
        ) : null
      }
    >
      {!workspaceMemoryAvailable && (
        <div className="text-xs text-amber-600 dark:text-amber-400 mb-3">
          {t("memory.builder.unavailable")}
        </div>
      )}
      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => handleToggle(e.target.checked)}
          disabled={!workspaceMemoryAvailable}
          className="mt-0.5 w-4 h-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <div className="flex-1">
          <div className="text-[13px] font-medium text-gray-700 dark:text-gray-300">
            {t("memory.builder.toggle")}
          </div>
          <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
            {t("memory.builder.toggleHint")}
          </div>
        </div>
      </label>

      {/* Strategy checkboxes */}
      {value.enabled && (
        <div className="ml-6 mt-3 space-y-2">
          {strategies.map(({ key, label, hint }) => (
            <label key={key} className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={value.strategies.includes(key)}
                onChange={() => handleStrategyToggle(key)}
                disabled={!value.enabled || !workspaceMemoryAvailable}
                className="mt-0.5 w-3.5 h-3.5 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
              />
              <div>
                <span className="text-[13px] text-gray-700 dark:text-gray-300">{label}</span>
                <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5">{hint}</p>
              </div>
            </label>
          ))}
        </div>
      )}
    </Section>
  );
}
