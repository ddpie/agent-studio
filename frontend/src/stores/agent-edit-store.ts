import { create } from "zustand";
import { fetchAgentMetadata, type AgentMetadata } from "../lib/agent-metadata";
import { extractToolsFromDeployment } from "../lib/tool-extractor";
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";
import { fetchToolCatalog } from "../lib/s3-utils";

interface AgentEditState {
  agentId: string | null;
  agentName: string | null;
  formData: Partial<AgentMetadata> | null;
  originalData: Partial<AgentMetadata> | null;
  loading: boolean;
  saving: boolean;

  loadAgent: (agentId: string, agentName: string) => Promise<void>;
  openNewWithData: (data: Partial<AgentMetadata>) => void;
  updateField: <K extends keyof AgentMetadata>(key: K, value: AgentMetadata[K]) => void;
  setSaving: (saving: boolean) => void;
  hasChanges: () => boolean;
  markSaved: () => void;
  getChangedFields: () => Record<string, { old: string; new: string }>;
}

/**
 * Fallback: fetch basic info from AgentCore control plane if S3 metadata is missing.
 */
async function fetchAgentFromControlPlane(agentId: string): Promise<Partial<AgentMetadata> | null> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return null;

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "bedrock-agentcore",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(`https://bedrock-agentcore-control.${agentConfig.region}.amazonaws.com/runtimes/${agentId}`);
    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const response = await fetch(url.toString(), {
      method: "GET",
      headers: signed.headers as Record<string, string>,
    });

    if (!response.ok) return null;
    const data = await response.json();
    return {
      name: data.agentRuntimeName || agentId,
      description: data.description || "",
      welcome_message: "",
      suggestions: [],
      tools: [],
      template_id: "",
      supports_images: false,
      system_prompt: "",
    } as Partial<AgentMetadata>;
  } catch {
    return null;
  }
}

/**
 * Inject missing built-in tool code from catalog.
 * For each tool in tool_names that has no @tool function in tool_definitions,
 * look it up in the catalog and append its code.
 */
async function injectBuiltinToolCode(data: Partial<AgentMetadata>): Promise<void> {
  const toolNames = (data.tool_names as string || "").split(",").map(t => t.trim()).filter(Boolean);
  if (!toolNames.length) return;

  const defs = data.tool_definitions || "";
  const definedFuncs = new Set([...(defs).matchAll(/@tool\s*\ndef\s+(\w+)\s*\(/g)].map(m => m[1]));

  const missing = toolNames.filter(n => !definedFuncs.has(n));
  if (!missing.length) return;

  try {
    const catalog = await fetchToolCatalog();
    const codeParts: string[] = [];
    for (const name of missing) {
      if (catalog[name]) {
        codeParts.push(catalog[name].code);
      }
    }
    if (codeParts.length) {
      const injected = codeParts.join("\n\n");
      data.tool_definitions = defs ? defs.trimEnd() + "\n\n" + injected : injected;
    }
  } catch (e) {
    console.warn("Failed to fetch tool catalog:", e);
  }
}

export const useAgentEditStore = create<AgentEditState>((set, get) => ({
  agentId: null,
  agentName: null,
  formData: null,
  originalData: null,
  loading: false,
  saving: false,

  hasChanges: () => {
    const { formData, originalData } = get();
    if (!formData || !originalData) return false;
    return JSON.stringify(formData) !== JSON.stringify(originalData);
  },

  loadAgent: async (agentId, agentName) => {
    set({ agentId: agentId, agentName: agentName, loading: true, formData: null });

    // Try S3 metadata first, fallback to control plane
    let metadata = await fetchAgentMetadata(agentId);
    if (!metadata) {
      console.warn(`No S3 metadata for ${agentId}, falling back to control plane`);
      metadata = (await fetchAgentFromControlPlane(agentId)) as AgentMetadata | null;
    }
    if (!metadata) {
      console.warn(`Control plane fallback also failed for ${agentId}`);
    }

    const data: Partial<AgentMetadata> = metadata || { name: agentName };

    // Extract tools from deployment.zip if:
    // 1. No tool_definitions at all, OR
    // 2. tool_definitions has fewer @tool functions than the tools[] list (partial/stale metadata)
    const toolCount = (data.tool_definitions || "").split("@tool").length - 1;
    const expectedCount = (data.tools || []).length;
    if (!data.tool_definitions || (expectedCount > 0 && toolCount < expectedCount)) {
      const name = data.name || agentName || "";
      if (name) {
        const extracted = await extractToolsFromDeployment(name);
        if (extracted) {
          data.tool_definitions = extracted.tool_definitions;
          if (!data.tool_names) data.tool_names = extracted.tool_names;
        }
      }
    }

    // Inject missing built-in tool code from catalog
    await injectBuiltinToolCode(data);

    set({ formData: data, originalData: JSON.parse(JSON.stringify(data)), loading: false });
  },

  openNewWithData: (data: Partial<AgentMetadata>) => {
    const draftId = `draft-${crypto.randomUUID().slice(0, 8)}`;
    // Inject built-in tool code async, update formData when done
    set({
      agentId: draftId,
      agentName: (data.display_name || data.name || "New Agent") as string,
      formData: data,
      originalData: JSON.parse(JSON.stringify(data)),
      loading: false,
    });
    injectBuiltinToolCode(data).then(() => {
      set({ formData: { ...data }, originalData: JSON.parse(JSON.stringify(data)) });
    });
  },

  updateField: (key, value) => {
    const { formData } = get();
    if (formData) {
      set({ formData: { ...formData, [key]: value } });
    }
  },

  setSaving: (saving) => set({ saving }),

  markSaved: () => {
    const { formData } = get();
    if (formData) {
      set({ originalData: JSON.parse(JSON.stringify(formData)) });
    }
  },

  getChangedFields: () => {
    const { formData, originalData } = get();
    if (!formData || !originalData) return {};
    const changes: Record<string, { old: string; new: string }> = {};
    const keys = new Set([...Object.keys(formData), ...Object.keys(originalData)]);
    for (const key of keys) {
      const oldVal = String((originalData as Record<string, unknown>)[key] ?? "");
      const newVal = String((formData as Record<string, unknown>)[key] ?? "");
      if (oldVal !== newVal) {
        changes[key] = { old: oldVal, new: newVal };
      }
    }
    return changes;
  },
}));
