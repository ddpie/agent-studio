import { useTranslation } from "react-i18next";
import { Code2, MessageSquare, Settings2, Sparkles, Network } from "lucide-react";
import { MODEL_GROUPS } from "../../lib/models";
import { useEditAssistantStore } from "../../stores/edit-assistant-store";
import type { AgentMetadata, AgentSkillEntry } from "../../lib/agent-metadata";
import Section from "./shared/Section";
import Field from "./shared/Field";
import SkillsSection from "./SkillsSection";
import ToolsEditor from "./ToolsEditor";
import SecretsSection from "./SecretsSection";
import McpTargetSelector from "./McpTargetSelector";
import LinkedAgentsSection from "./LinkedAgentsSection";
import MemorySection from "./MemorySection";

export const TEMPLATE_OPTIONS = [
  { id: "", label: "None" },
  { id: "general", label: "General Assistant" },
  { id: "expert", label: "Professional Consultant" },
  { id: "customer_service", label: "Customer Service" },
  { id: "data_analyst", label: "Data Analyst" },
  { id: "creative_writer", label: "Creative Writer" },
];

export const inputClass = "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";
export const disabledClass = "w-full px-2 py-1.5 border border-gray-100 dark:border-gray-700 rounded-lg text-[13px] bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-500 cursor-not-allowed";

interface AgentFormSectionsProps {
  formData: Partial<AgentMetadata>;
  agentId: string;
  agentName: string | null;
  isCreateMode: boolean;
  changedFields: Record<string, { old: string; new: string }>;
  updateField: <K extends keyof AgentMetadata>(key: K, value: AgentMetadata[K]) => void;
  handleOptimizeField: (fieldName: string, fieldLabel: string) => void;
  deployedSkillHashes?: Record<string, string>;
  onEditSkill?: (skill: AgentSkillEntry) => void;
}

export default function AgentFormSections({
  formData, agentId, agentName, isCreateMode, changedFields,
  updateField, handleOptimizeField, deployedSkillHashes, onEditSkill,
}: AgentFormSectionsProps) {
  const { t } = useTranslation();

  return (
    <>
      {/* Basic Info */}
      <Section title={t("agentEditor.basicInfo")} icon={<Settings2 className="w-3.5 h-3.5" />}>
        <div className="grid grid-cols-2 gap-4">
          <Field label={t("agentEditor.name")} changed={!!changedFields.name} hint={isCreateMode ? t("agentEditor.nameHint") : t("agentEditor.nameFixed")}>
            <input
              type="text"
              value={formData.name || agentName || ""}
              onChange={isCreateMode ? (e) => updateField("name", e.target.value) : undefined}
              disabled={!isCreateMode}
              className={isCreateMode ? inputClass : disabledClass}
            />
          </Field>
          <Field label={t("agentEditor.displayName")} changed={!!changedFields.display_name} hint={t("agentEditor.displayNameHint")} onOptimize={() => handleOptimizeField("display_name", "Display Name")}>
            <input
              type="text"
              value={formData.display_name || ""}
              onChange={(e) => updateField("display_name", e.target.value)}
              placeholder={formData.name || agentName || ""}
              className={inputClass}
            />
          </Field>
        </div>
        <Field label={t("agentEditor.description")} changed={!!changedFields.description} onOptimize={() => handleOptimizeField("description", "Description")}>
          <textarea
            value={formData.description || ""}
            onChange={(e) => updateField("description", e.target.value)}
            rows={2}
            className={inputClass + " resize-none"}
            placeholder={t("agentEditor.descriptionHint")}
          />
        </Field>
      </Section>

      {/* Chat Settings */}
      <Section title={t("agentEditor.chatSettings")} icon={<MessageSquare className="w-3.5 h-3.5" />}>
        <Field label={t("agentEditor.welcomeMessage")} changed={!!changedFields.welcome_message} hint={t("agentEditor.welcomeMessageHint")} onOptimize={() => handleOptimizeField("welcome_message", "Welcome Message")}>
          <textarea
            value={formData.welcome_message || ""}
            onChange={(e) => updateField("welcome_message", e.target.value)}
            rows={2}
            className={inputClass + " resize-none"}
            placeholder={t("agentEditor.welcomeMessageHint")}
          />
        </Field>
        <Field label={t("agentEditor.suggestions")} changed={!!changedFields.suggestions} hint={t("agentEditor.suggestionsHint")} onOptimize={() => handleOptimizeField("suggestions", "Suggested Prompts")}>
          <textarea
            value={(Array.isArray(formData.suggestions) ? formData.suggestions : (formData.suggestions || "").split("|").filter(Boolean)).join("\n")}
            onChange={(e) => updateField("suggestions", e.target.value.split("\n").filter(Boolean))}
            rows={3}
            className={inputClass + " resize-none"}
            placeholder={"What can you do?\nHelp me with...\nShow me an example"}
          />
        </Field>
      </Section>

      {/* Agent Behavior */}
      <Section title={t("agentEditor.agentBehavior")} icon={<Settings2 className="w-3.5 h-3.5" />}>
        <div className="grid grid-cols-2 gap-4">
          <Field label={t("agentEditor.defaultModel")} changed={!!changedFields.default_model_id} hint={t("agentEditor.modelHint")}>
            <select
              value={formData.default_model_id || ""}
              onChange={(e) => updateField("default_model_id", e.target.value)}
              className={inputClass}
            >
              <option value="">{t("agentFormSections.autoInherit")}</option>
              {MODEL_GROUPS.map((g) =>
                g.models.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))
              )}
            </select>
          </Field>
          <Field label={t("agentEditor.imageSupport")} changed={!!changedFields.supports_images}>
            <label className={`flex items-center gap-2 h-[34px] px-3 border rounded-lg cursor-pointer transition-colors ${formData.supports_images ? "bg-blue-50 dark:bg-blue-900/30 border-blue-300 dark:border-blue-700 text-blue-700 dark:text-blue-300" : "border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300"}`}>
              <input
                type="checkbox"
                checked={formData.supports_images || false}
                onChange={(e) => updateField("supports_images", e.target.checked)}
                className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
              />
              <span className="text-[13px]">{formData.supports_images ? t("agentEditor.multimodalEnabled") : t("agentEditor.multimodal")}</span>
            </label>
          </Field>
        </div>
        <Field label={t("agentEditor.systemPrompt")} changed={!!changedFields.system_prompt} hint={t("agentEditor.systemPromptHint")} onOptimize={() => handleOptimizeField("system_prompt", "System Prompt")}>
          <textarea
            value={formData.system_prompt || ""}
            onChange={(e) => updateField("system_prompt", e.target.value)}
            rows={14}
            className={inputClass + " font-mono text-xs leading-relaxed resize-y"}
            placeholder={t("agentEditor.systemPromptPlaceholder")}
          />
        </Field>
      </Section>

      {/* Skills */}
      <SkillsSection
        skills={formData.skills || []}
        agentId={agentId}
        deployedHashes={deployedSkillHashes}
        onEditSkill={onEditSkill}
      />

      {/* Tools */}
      <Section title={t("agentEditor.tools")} icon={<Code2 className="w-3.5 h-3.5" />} action={
        <button
          onClick={() => handleOptimizeField("tool_definitions", "Tools")}
          className="p-0.5 text-gray-300 dark:text-gray-600 hover:text-purple-500 transition-colors"
          title={t("agentEditor.optimize", { label: "tools" })}
        >
          <Sparkles className="w-3 h-3" />
        </button>
      }>
        <ToolsEditor
          value={formData.tool_definitions || ""}
          onChange={(defs, names) => {
            updateField("tool_definitions", defs);
            updateField("tool_names", names);
            updateField("tools", names.split(",").map(s => s.trim()).filter(Boolean));
          }}
          onOptimizeTool={(toolName, toolCode) => {
            const { sendMessage, openPanel } = useEditAssistantStore.getState();
            openPanel(agentId);
            sendMessage(
              `Optimize the tool \`${toolName}\`. Current code:\n\`\`\`python\n${toolCode}\n\`\`\`\n\nImprove:\n1. Docstring: clear, describes purpose, args, and return value\n2. Type hints: complete for all parameters and return\n3. Error handling: handle common failures (timeout, permission denied, empty results)\n4. Code quality: concise, no unnecessary comments\nOnly output this one tool. Output using __field_value:tool_definitions format.`,
              { ...formData },
              (updates) => {
                for (const [key, value] of Object.entries(updates)) {
                  if (key === "tool_definitions" && typeof value === "string" && formData.tool_definitions) {
                    const existingBlocks = (formData.tool_definitions).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
                    const newBlocks = (value as string).split(/\n(?=@tool\b)/).map(s => s.trim()).filter(Boolean);
                    const merged = new Map<string, string>();
                    for (const b of existingBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0, 30); merged.set(n, b); }
                    for (const b of newBlocks) { const n = b.match(/def\s+(\w+)\s*\(/)?.[1] || b.slice(0, 30); merged.set(n, b); }
                    updateField("tool_definitions" as keyof typeof formData, Array.from(merged.values()).join("\n\n\n") as never);
                  } else {
                    updateField(key as keyof typeof formData, value as never);
                  }
                }
              }
            );
          }}
        />
      </Section>

      {/* MCP Tools */}
      <Section title={t("agentEdit.mcpTargets")} icon={<Network className="w-3.5 h-3.5" />}>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          {t("agentEdit.mcpTargetsDesc")}
        </p>
        <McpTargetSelector
          selectedTargets={formData.mcp_targets || []}
          onChange={(targets) => updateField("mcp_targets", targets)}
          hasLegacyConfig={!!formData.gateway_url && !formData.mcp_targets?.length}
        />
      </Section>

      {/* Linked Agents — let this agent call other workspace peers via A2A */}
      {!isCreateMode && (
        <LinkedAgentsSection
          agentId={agentId}
          linkedAgents={formData.linked_agents || []}
          onChange={(next) => updateField("linked_agents", next)}
        />
      )}

      {/* Memory */}
      {!isCreateMode && (
        <MemorySection
          agentId={agentId}
          value={formData.memory || { enabled: false, strategies: [] }}
          onChange={(memory) => updateField("memory", memory)}
          workspaceMemoryAvailable={true}
        />
      )}

      {/* Secrets */}
      {!isCreateMode && (
        <SecretsSection agentId={agentId} />
      )}
    </>
  );
}
