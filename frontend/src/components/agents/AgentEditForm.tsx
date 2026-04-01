import { useAgentEditStore } from "../../stores/agent-edit-store";
import { useAgentListStore } from "../../stores/agent-list-store";
import { useEditAssistantStore } from "../../stores/edit-assistant-store";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { Loader2, Save, Plus, Trash2, Eye, EyeOff, Code2, MessageSquare, Settings2, Shield, AlertTriangle, Sparkles, Maximize2, Minimize2, FileDown } from "lucide-react";
import { useState, useMemo, useRef, useEffect } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { vscodeDark } from "@uiw/codemirror-theme-vscode";
import { linter, type Diagnostic } from "@codemirror/lint";
import { preloadPyodide, isPyodideReady, checkPythonSyntax } from "../../lib/pyodide-checker";
import { MODEL_GROUPS } from "../../lib/models";
import { writeJsonToS3 } from "../../lib/s3-storage";
import EditAssistant from "./EditAssistant";

/** Combined linter: Pyodide compile() when ready + structural checks always */
const toolLinter = linter((view) => {
  const doc = view.state.doc.toString();
  if (!doc.trim()) return [];

  // Start loading Pyodide if not already
  preloadPyodide();

  const diagnostics: Diagnostic[] = [];

  // Real Python syntax check via Pyodide (if loaded)
  if (isPyodideReady()) {
    const pyErrors = checkPythonSyntax(doc);
    for (const err of pyErrors) {
      const lineNum = Math.min(err.line, view.state.doc.lines);
      const line = view.state.doc.line(lineNum);
      const col = Math.min(Math.max(err.col - 1, 0), line.length);
      diagnostics.push({
        from: line.from + col,
        to: line.to,
        severity: "error",
        message: err.msg,
      });
    }
  }

  // Structural checks (always run)
  if (!doc.includes("@tool")) {
    diagnostics.push({ from: 0, to: Math.min(doc.length, 5), severity: "error", message: "Missing @tool decorator" });
  }

  const defMatch = doc.match(/def\s+(\w+)\s*\(/);
  if (!defMatch) {
    diagnostics.push({ from: 0, to: Math.min(doc.length, 5), severity: "error", message: "Missing function definition (def ...)" });
  } else {
    const defLine = doc.split("\n").find(l => l.trimStart().startsWith("def "));
    if (defLine && !defLine.includes("->")) {
      const idx = doc.indexOf(defLine);
      diagnostics.push({ from: idx, to: idx + defLine.length, severity: "warning", message: "Missing return type hint (e.g., -> str)" });
    }
    if (!doc.includes('"""') && !doc.includes("'''")) {
      diagnostics.push({ from: 0, to: 0, severity: "warning", message: "Missing docstring — required for tool description" });
    }
  }

  // Bracket balance
  let parens = 0, brackets = 0, braces = 0;
  for (const ch of doc) {
    if (ch === "(") parens++; else if (ch === ")") parens--;
    else if (ch === "[") brackets++; else if (ch === "]") brackets--;
    else if (ch === "{") braces++; else if (ch === "}") braces--;
  }
  if (parens !== 0) diagnostics.push({ from: 0, to: 0, severity: "error", message: `Unbalanced parentheses: ${parens > 0 ? `${parens} unclosed (` : `${-parens} extra )`}` });
  if (brackets !== 0) diagnostics.push({ from: 0, to: 0, severity: "error", message: `Unbalanced brackets: ${brackets > 0 ? `${brackets} unclosed [` : `${-brackets} extra ]`}` });
  if (braces !== 0) diagnostics.push({ from: 0, to: 0, severity: "error", message: `Unbalanced braces: ${braces > 0 ? `${braces} unclosed {` : `${-braces} extra }`}` });

  return diagnostics;
});

const TEMPLATE_OPTIONS = [
  { id: "", label: "None" },
  { id: "general", label: "General Assistant" },
  { id: "expert", label: "Professional Consultant" },
  { id: "customer_service", label: "Customer Service" },
  { id: "data_analyst", label: "Data Analyst" },
  { id: "creative_writer", label: "Creative Writer" },
];

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 bg-gradient-to-r from-gray-50 to-white flex items-center gap-1.5">
        {icon && <span className="text-gray-400">{icon}</span>}
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

const inputClass = "w-full px-2 py-1.5 border border-gray-200 rounded-lg text-[13px] focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all";
const disabledClass = "w-full px-2 py-1.5 border border-gray-100 rounded-lg text-[13px] bg-gray-50 text-gray-400 cursor-not-allowed";

export default function AgentEditForm() {
  const {
    editingAgentId, editingAgentName, formData, loading, saving,
    closeEdit, updateField, setSaving,
  } = useAgentEditStore();
  const { fetchAgents } = useAgentListStore();
  const { panelOpen, openPanel } = useEditAssistantStore();
  const [status, setStatus] = useState<string | null>(null);
  const [savingDraft, setSavingDraft] = useState(false);

  // Auto-open AI assistant panel when editing
  useEffect(() => {
    if (editingAgentId && !panelOpen) {
      openPanel(editingAgentId);
    }
  }, [editingAgentId]);

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
        : (formData.suggestions || "");

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
- tool_definitions: ${formData.tool_definitions || ""}
- tool_names: ${formData.tool_names || (formData.tools || []).join(",") || ""}
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

  const handleSaveDraft = async () => {
    setSavingDraft(true);
    setStatus(null);
    try {
      const draftKey = isCreateMode
        ? `drafts/${formData.name || "untitled"}/metadata.json`
        : `agents/${editingAgentId}/draft.json`;
      const draftData = {
        ...formData,
        _draftSavedAt: new Date().toISOString(),
        _agentId: editingAgentId,
        _agentName: editingAgentName,
      };
      const ok = await writeJsonToS3(draftKey, draftData);
      setStatus(ok ? "Draft saved" : "Failed to save draft");
    } catch (err) {
      setStatus(`Error: ${err instanceof Error ? err.message : "Unknown"}`);
    } finally {
      setSavingDraft(false);
    }
  };

  return (
    <div className="flex h-full">
    <div className="flex flex-col flex-1 min-w-0 bg-gray-50/50">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-white">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">
            {isCreateMode ? "Create Agent" : (formData.display_name || editingAgentName)}
          </h2>
          <p className="text-[11px] text-gray-400">{isCreateMode ? "Configure and deploy a new agent" : "Edit agent configuration"}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => openPanel(editingAgentId!)}
            className={`p-1.5 rounded-lg transition-colors ${panelOpen ? "bg-purple-100 text-purple-600" : "text-gray-400 hover:text-purple-600 hover:bg-purple-50"}`}
            title="AI Assistant"
          >
            <Sparkles className="w-4 h-4" />
          </button>
          <button
            onClick={handleSaveDraft}
            disabled={savingDraft}
            className="flex items-center gap-1 px-3 py-1.5 text-[13px] text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            title="Save draft without deploying"
          >
            {savingDraft ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileDown className="w-3.5 h-3.5" />}
            Draft
          </button>
          <button
            onClick={closeEdit}
            className="px-3 py-1.5 text-[13px] text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-1.5 text-[13px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {isCreateMode ? "Create" : "Update"}
          </button>
        </div>
      </div>

      {/* Form */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {status && (
          <div className={`px-4 py-3 rounded-lg text-sm font-medium ${status.includes("Error") || status.includes("failed") ? "bg-red-50 text-red-600 border border-red-200" : "bg-green-50 text-green-600 border border-green-200"}`}>
            {status}
          </div>
        )}

        {/* Basic Info */}
        <Section title="Basic Info" icon={<Settings2 className="w-3.5 h-3.5" />}>
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
            <textarea
              value={formData.description || ""}
              onChange={(e) => updateField("description", e.target.value)}
              rows={2}
              className={inputClass + " resize-none"}
              placeholder="Brief description of what this agent does"
            />
          </Field>
        </Section>

        {/* Chat Settings */}
        <Section title="Chat Settings" icon={<MessageSquare className="w-3.5 h-3.5" />}>
          <Field label="Welcome Message" hint="First message shown when user opens this agent">
            <textarea
              value={formData.welcome_message || ""}
              onChange={(e) => updateField("welcome_message", e.target.value)}
              rows={2}
              className={inputClass + " resize-none"}
              placeholder="Hello! I can help you with..."
            />
          </Field>
          <Field label="Suggested Prompts" hint="One per line, shown as quick-start buttons">
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
        <Section title="Agent Behavior" icon={<Settings2 className="w-3.5 h-3.5" />}>
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
            <Field label="Default Model" hint="Auto-selected when chatting">
              <select
                value={formData.default_model_id || ""}
                onChange={(e) => updateField("default_model_id", e.target.value)}
                className={inputClass}
              >
                <option value="">Auto (inherit)</option>
                {MODEL_GROUPS.map((g) =>
                  g.models.map((m) => (
                    <option key={m.id} value={m.id}>{m.label}</option>
                  ))
                )}
              </select>
            </Field>
            <Field label="Image Support">
              <label className="flex items-center gap-2 h-[34px] px-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50 transition-colors">
                <input
                  type="checkbox"
                  checked={formData.supports_images || false}
                  onChange={(e) => updateField("supports_images", e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-[13px] text-gray-600">Multimodal</span>
              </label>
            </Field>
          </div>
          <Field label="System Prompt" hint="Defines the agent's personality and behavior. Changes trigger a redeploy (1-2 min).">
            <textarea
              value={formData.system_prompt || ""}
              onChange={(e) => updateField("system_prompt", e.target.value)}
              rows={14}
              className={inputClass + " font-mono text-xs leading-relaxed resize-y"}
              placeholder="You are a helpful assistant that..."
            />
          </Field>
        </Section>

        {/* Tools */}
        <Section title="Tools" icon={<Code2 className="w-3.5 h-3.5" />}>
          <ToolsEditor
            value={formData.tool_definitions || ""}
            onChange={(defs, names) => {
              updateField("tool_definitions", defs);
              updateField("tool_names", names);
              updateField("tools", names.split(",").map(s => s.trim()).filter(Boolean));
            }}
          />
        </Section>

        {/* Secrets */}
        {!isCreateMode && (
          <SecretsSection agentId={editingAgentId!} />
        )}
      </div>
    </div>
    {/* AI Assistant sidebar */}
    <EditAssistant />
    </div>
  );
}

/** Extract function name from a @tool code block */
function extractFuncName(code: string): string {
  const m = code.match(/def\s+(\w+)\s*\(/);
  return m ? m[1] : "unnamed";
}

/** Extract first line of docstring as description */
function extractDocstring(code: string): string {
  const m = code.match(/"""(.+?)"""|'''(.+?)'''/s);
  if (!m) return "";
  const raw = (m[1] || m[2]).trim();
  // Take first line only
  const firstLine = raw.split("\n")[0].trim();
  return firstLine.length > 60 ? firstLine.slice(0, 60) + "..." : firstLine;
}

/** Split combined tool_definitions into individual tool blocks */
function splitTools(defs: string): string[] {
  if (!defs.trim()) return [];
  // Split on @tool that starts a new block (look-ahead)
  const parts = defs.split(/\n(?=@tool\b)/);
  return parts.map(p => p.trim()).filter(Boolean);
}

/** Combine individual tool blocks into one string */
function joinTools(blocks: string[]): { defs: string; names: string } {
  const defs = blocks.filter(Boolean).join("\n\n\n");
  const names = blocks.filter(Boolean).map(extractFuncName).filter(n => n !== "unnamed").join(",");
  return { defs, names };
}

const TOOL_TEMPLATE = `@tool
def my_tool(query: str) -> str:
    """Description of what this tool does.

    Args:
        query: The input parameter.

    Returns:
        Result as string.
    """
    return "result"`;

function ToolsEditor({ value, onChange }: {
  value: string;
  onChange: (defs: string, names: string) => void;
}) {
  const [blocks, setBlocks] = useState<string[]>(() => {
    const initial = splitTools(value);
    return initial.length > 0 ? initial : [];
  });

  // Sync blocks when value changes externally (e.g., from AI assistant __update)
  // Compare by splitting — avoids false triggers from whitespace differences
  const prevValueRef = useRef(value);
  const internalUpdateRef = useRef(false);
  useEffect(() => {
    if (internalUpdateRef.current) {
      internalUpdateRef.current = false;
      prevValueRef.current = value;
      return;
    }
    if (value !== prevValueRef.current) {
      prevValueRef.current = value;
      const newBlocks = splitTools(value);
      setBlocks(newBlocks.length > 0 ? newBlocks : []);
    }
  }, [value]);

  const sync = (updated: string[]) => {
    setBlocks(updated);
    const { defs, names } = joinTools(updated);
    internalUpdateRef.current = true; // Mark as internal update to skip useEffect
    onChange(defs, names);
  };

  const updateBlock = (idx: number, code: string) => {
    const updated = [...blocks];
    updated[idx] = code;
    sync(updated);
  };

  const removeBlock = (idx: number) => {
    sync(blocks.filter((_, i) => i !== idx));
  };

  const addBlock = () => {
    sync([...blocks, TOOL_TEMPLATE]);
  };

  const [collapsed, setCollapsed] = useState<Record<number, boolean>>(() => {
    // Default all tools to collapsed
    const initial: Record<number, boolean> = {};
    blocks.forEach((_, i) => { initial[i] = true; });
    return initial;
  });
  const [fullscreenIdx, setFullscreenIdx] = useState<number | null>(null);

  const toggleCollapse = (idx: number) => {
    setCollapsed((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  // Fullscreen overlay
  if (fullscreenIdx !== null && blocks[fullscreenIdx] !== undefined) {
    const code = blocks[fullscreenIdx];
    const name = extractFuncName(code);
    const desc = extractDocstring(code);
    return (
      <div className="fixed inset-0 z-50 bg-gray-900 flex flex-col">
        <div className="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
          <span className="text-sm font-mono text-gray-200">
            <span className="text-blue-400">@tool</span> {name}
            {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
          </span>
          <button
            onClick={() => setFullscreenIdx(null)}
            className="flex items-center gap-1 px-2 py-1 text-xs text-gray-400 hover:text-white bg-gray-700 rounded hover:bg-gray-600 transition-colors"
          >
            <Minimize2 className="w-3.5 h-3.5" /> Exit Fullscreen
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <CodeMirror
            value={code}
            onChange={(v) => updateBlock(fullscreenIdx, v)}
            extensions={[python(), toolLinter]}
            theme={vscodeDark}
            height="100%"
            style={{ fontSize: "13px", height: "100%" }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {blocks.length === 0 && (
        <p className="text-xs text-gray-400 italic">No tools defined. Click "Add Tool" to get started.</p>
      )}
      {blocks.map((code, idx) => {
        const name = extractFuncName(code);
        const desc = extractDocstring(code);
        const isCollapsed = collapsed[idx] ?? false;
        return (
          <div key={idx} className="rounded-lg border border-gray-200 overflow-hidden">
            <div
              className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700 cursor-pointer select-none"
              onClick={() => toggleCollapse(idx)}
            >
              <span className="text-xs font-mono text-gray-300 truncate">
                <span className="text-gray-500 mr-1">{isCollapsed ? "▶" : "▼"}</span>
                <span className="text-blue-400">@tool</span> {name !== "unnamed" ? name : <span className="text-gray-500 italic">unnamed</span>}
                {desc && <span className="text-gray-500 font-sans ml-2">— {desc}</span>}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={(e) => { e.stopPropagation(); setFullscreenIdx(idx); }}
                  className="p-1 text-gray-500 hover:text-white transition-colors"
                  title="Fullscreen"
                >
                  <Maximize2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); removeBlock(idx); }}
                  className="p-1 text-gray-500 hover:text-red-400 transition-colors"
                  title="Remove tool"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            {!isCollapsed && (
              <CodeMirror
                value={code}
                onChange={(v) => updateBlock(idx, v)}
                extensions={[python(), toolLinter]}
                theme={vscodeDark}
                minHeight="120px"
                maxHeight="400px"
                style={{ fontSize: "12px" }}
              />
            )}
          </div>
        );
      })}
      <button
        onClick={addBlock}
        className="flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:text-blue-700 px-3 py-2 border border-dashed border-blue-300 rounded-lg hover:bg-blue-50 transition-colors w-full justify-center"
      >
        <Plus className="w-3.5 h-3.5" /> Add Tool
      </button>
    </div>
  );
}

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
            <button onClick={() => setShowValues(!showValues)} className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600">
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
