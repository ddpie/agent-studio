import { useAgentEditStore } from "../../stores/agent-edit-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { Loader2, Save } from "lucide-react";
import { useState } from "react";

const TEMPLATE_OPTIONS = [
  { id: "", label: "None" },
  { id: "general", label: "General Assistant" },
  { id: "expert", label: "Professional Consultant" },
  { id: "customer_service", label: "Customer Service" },
  { id: "data_analyst", label: "Data Analyst" },
  { id: "creative_writer", label: "Creative Writer" },
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="px-3 py-1.5 border-b border-gray-100 bg-gray-50/50">
        <h3 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">{title}</h3>
      </div>
      <div className="px-3 py-3 space-y-3">{children}</div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[11px] font-medium text-gray-500 mb-0.5">{label}</label>
      {children}
      {hint && <p className="text-[10px] text-gray-400 mt-0.5">{hint}</p>}
    </div>
  );
}

const inputClass = "w-full px-2 py-1 border border-gray-300 rounded text-[13px] focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none";
const disabledClass = "w-full px-2 py-1 border border-gray-200 rounded text-[13px] bg-gray-50 text-gray-400";

export default function AgentEditForm() {
  const {
    editingAgentId, editingAgentName, formData, loading, saving,
    closeEdit, updateField, setSaving,
  } = useAgentEditStore();
  const { fetchAgents } = useAgentListStore();
  const [status, setStatus] = useState<string | null>(null);

  if (!editingAgentId || loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        {loading ? <Loader2 className="w-6 h-6 animate-spin" /> : null}
      </div>
    );
  }

  if (!formData) return null;

  const isCreateMode = editingAgentId === "__new__";

  const handleSave = async () => {
    setSaving(true);
    setStatus(null);

    try {
      const suggestions = Array.isArray(formData.suggestions)
        ? formData.suggestions.join("|")
        : "";

      const prompt = isCreateMode
        ? `Execute create_agent with these exact parameters:
- agent_name: ${formData.name || editingAgentName}
- description: ${formData.description || ""}
- system_prompt: ${formData.system_prompt || ""}
- tool_definitions: ${formData.tool_definitions || ""}
- tool_names: ${formData.tool_names || ""}
- welcome_message: ${formData.welcome_message || ""}
- suggestions: ${suggestions}
- template_id: ${formData.template_id || ""}
- supports_images: ${formData.supports_images || false}
- permission_tier: readonly

Do NOT ask for confirmation. Execute create_agent immediately with these parameters.`
        : `Execute update_agent with these exact parameters:
- agent_id: ${editingAgentId}
- agent_name: ${formData.name || editingAgentName}
- display_name: ${formData.display_name || editingAgentName}
- description: ${formData.description || ""}
- system_prompt: ${formData.system_prompt || ""}
- welcome_message: ${formData.welcome_message || ""}
- suggestions: ${suggestions}
- template_id: ${formData.template_id || ""}
- supports_images: ${formData.supports_images || false}

Do NOT ask for confirmation. Execute update_agent immediately with these parameters.`;

      let result = "";
      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
      for await (const chunk of stream) {
        result += chunk;
      }

      setStatus(result.includes("error") ? (isCreateMode ? "Create failed" : "Update failed") : (isCreateMode ? "Created successfully" : "Updated successfully"));
      fetchAgents();
      if (isCreateMode && !result.includes("error")) closeEdit();
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 bg-white">
        <div>
          <h2 className="text-base font-semibold text-gray-900">
            {isCreateMode ? "Create Agent" : (formData.display_name || editingAgentName)}
          </h2>
          <p className="text-xs text-gray-500">{isCreateMode ? "Review and create agent" : "Edit agent configuration"}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={closeEdit}
            className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {isCreateMode ? "Create" : "Update"}
          </button>
        </div>
      </div>

      {/* Form */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {status && (
          <div className={`px-3 py-2 rounded-lg text-sm ${status.includes("Error") || status.includes("failed") ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"}`}>
            {status}
          </div>
        )}

        {/* Basic Info */}
        <Section title="Basic Info">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Name" hint={isCreateMode ? "Alphanumeric only, max 36 chars" : "Cannot be changed after creation"}>
              <input
                type="text"
                value={formData.name || editingAgentName || ""}
                onChange={isCreateMode ? (e) => updateField("name", e.target.value) : undefined}
                disabled={!isCreateMode}
                className={isCreateMode ? inputClass : disabledClass}
              />
            </Field>
            <Field label="Display Name" hint="Shown in sidebar and chat header">
              <input
                type="text"
                value={formData.display_name || ""}
                onChange={(e) => updateField("display_name", e.target.value)}
                placeholder={formData.name || editingAgentName || ""}
                className={inputClass}
              />
            </Field>
          </div>
          <Field label="Description">
            <input
              type="text"
              value={formData.description || ""}
              onChange={(e) => updateField("description", e.target.value)}
              className={inputClass}
            />
          </Field>
        </Section>

        {/* Chat Settings */}
        <Section title="Chat Settings">
          <Field label="Welcome Message">
            <input
              type="text"
              value={formData.welcome_message || ""}
              onChange={(e) => updateField("welcome_message", e.target.value)}
              className={inputClass}
            />
          </Field>
          <Field label="Suggestions" hint="One per line, shown on welcome page">
            <textarea
              value={(formData.suggestions || []).join("\n")}
              onChange={(e) => updateField("suggestions", e.target.value.split("\n").filter(Boolean))}
              rows={3}
              className={inputClass + " resize-none"}
            />
          </Field>
        </Section>

        {/* Agent Behavior */}
        <Section title="Agent Behavior">
          <div className="grid grid-cols-3 gap-4">
            <Field label="Template">
              <select
                value={formData.template_id || ""}
                onChange={(e) => updateField("template_id", e.target.value)}
                className={inputClass}
              >
                {TEMPLATE_OPTIONS.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Image Support">
              <label className="flex items-center gap-2 h-[28px]">
                <input
                  type="checkbox"
                  checked={formData.supports_images || false}
                  onChange={(e) => updateField("supports_images", e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-[13px] text-gray-600">Enable multimodal</span>
              </label>
            </Field>
          </div>
          <Field label="System Prompt" hint="Changes here will trigger a redeploy (1-2 min)">
            <textarea
              value={formData.system_prompt || ""}
              onChange={(e) => updateField("system_prompt", e.target.value)}
              rows={14}
              className={inputClass + " font-mono resize-y"}
            />
          </Field>
        </Section>

        {/* Tools */}
        {formData.tools && formData.tools.length > 0 && (
          <Section title="Tools">
            <div className="flex flex-wrap gap-2">
              {formData.tools.map((t) => (
                <span key={t} className="px-2.5 py-1 bg-blue-50 text-blue-700 rounded-md text-xs font-medium">{t}</span>
              ))}
            </div>
            <p className="text-xs text-gray-400">Tool editing coming soon</p>
          </Section>
        )}
      </div>
    </div>
  );
}
