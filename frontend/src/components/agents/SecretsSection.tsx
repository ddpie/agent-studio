import { useState } from "react";
import { Loader2, Save, Plus, Trash2, Eye, EyeOff, Shield } from "lucide-react";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import Section from "./shared/Section";

const inputClass = "w-full px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all dark:bg-gray-800 dark:text-gray-100";

function SecretsSection({ agentId }: { agentId: string }) {
  const [secrets, setSecrets] = useState<Array<{ key: string; value: string }>>([]);
  const [saving, setSaving] = useState(false);
  const [showValues, setShowValues] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const addRow = () => setSecrets([...secrets, { key: "", value: "" }]);
  const removeRow = (idx: number) => setSecrets(secrets.filter((_, i) => i !== idx));
  const updateRow = (idx: number, field: "key" | "value", val: string) => {
    setSecrets(secrets.map((s, i) => i === idx ? { ...s, [field]: val } : s));
  };

  const saveSecrets = async () => {
    const valid = secrets.filter((s) => s.key && s.value);
    if (valid.length === 0) return;
    setSaving(true);
    setStatus(null);
    try {
      const secretsObj: Record<string, string> = {};
      for (const s of valid) secretsObj[s.key] = s.value;
      const prompt = `Execute set_agent_secrets with these parameters:
- agent_id: ${agentId}
- secrets: ${JSON.stringify(JSON.stringify(secretsObj))}

Do NOT ask for confirmation. Execute immediately.`;
      let result = "";
      const stream = invokeMetaAgent(prompt, [], undefined, undefined, undefined, undefined);
      for await (const chunk of stream) result += chunk;
      setStatus(result.includes("error") ? "Failed to save secrets" : "Secrets saved");
      if (!result.includes("error")) setSecrets(valid.map((s) => ({ key: s.key, value: "" })));
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section title="Secrets" icon={<Shield className="w-3.5 h-3.5" />}>
      {status && (
        <div className={`px-3 py-2 rounded-lg text-xs ${status.includes("Error") || status.includes("Failed") ? "bg-red-50 text-red-600 border border-red-200" : "bg-green-50 text-green-600 border border-green-200"}`}>
          {status}
        </div>
      )}
      <p className="text-[11px] text-gray-400">API keys stored in AWS Secrets Manager. Values are never shown after saving.</p>
      {secrets.map((s, idx) => (
        <div key={idx} className="flex gap-2 items-center">
          <input type="text" value={s.key} onChange={(e) => updateRow(idx, "key", e.target.value)}
            placeholder="KEY_NAME" className={inputClass + " flex-1 font-mono"} />
          <input type={showValues ? "text" : "password"} value={s.value} onChange={(e) => updateRow(idx, "value", e.target.value)}
            placeholder="value" className={inputClass + " flex-1"} />
          <button onClick={() => removeRow(idx)} className="p-1.5 text-gray-300 hover:text-red-500 transition-colors"><Trash2 className="w-3.5 h-3.5" /></button>
        </div>
      ))}
      <div className="flex items-center gap-3 mt-1">
        <button onClick={addRow} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium">
          <Plus className="w-3.5 h-3.5" /> Add secret
        </button>
        {secrets.length > 0 && (
          <>
            <button onClick={() => setShowValues(!showValues)} className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
              {showValues ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />} {showValues ? "Hide" : "Show"}
            </button>
            <button onClick={saveSecrets} disabled={saving}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 ml-auto font-medium shadow-sm">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save secrets
            </button>
          </>
        )}
      </div>
    </Section>
  );
}

export default SecretsSection;
