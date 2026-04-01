import { create } from "zustand";
import { fetchAgentMetadata, type AgentMetadata } from "../lib/agent-metadata";
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

interface AgentEditState {
  editingAgentId: string | null;
  editingAgentName: string | null;
  formData: Partial<AgentMetadata> | null;
  originalData: Partial<AgentMetadata> | null;
  loading: boolean;
  saving: boolean;

  openEdit: (agentId: string, agentName: string) => Promise<void>;
  openNewWithData: (data: Partial<AgentMetadata>) => void;
  closeEdit: () => void;
  updateField: <K extends keyof AgentMetadata>(key: K, value: AgentMetadata[K]) => void;
  setSaving: (saving: boolean) => void;
  hasChanges: () => boolean;
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

export const useAgentEditStore = create<AgentEditState>((set, get) => ({
  editingAgentId: null,
  editingAgentName: null,
  formData: null,
  originalData: null,
  loading: false,
  saving: false,

  hasChanges: () => {
    const { formData, originalData } = get();
    if (!formData || !originalData) return false;
    return JSON.stringify(formData) !== JSON.stringify(originalData);
  },

  openEdit: async (agentId, agentName) => {
    set({ editingAgentId: agentId, editingAgentName: agentName, loading: true, formData: null });

    // Try S3 metadata first, fallback to control plane
    let metadata = await fetchAgentMetadata(agentId);
    if (!metadata) {
      console.warn(`No S3 metadata for ${agentId}, falling back to control plane`);
      metadata = (await fetchAgentFromControlPlane(agentId)) as AgentMetadata | null;
    }
    if (!metadata) {
      console.warn(`Control plane fallback also failed for ${agentId}`);
    }

    const data = metadata || { name: agentName };
    set({ formData: data, originalData: JSON.parse(JSON.stringify(data)), loading: false });
  },

  closeEdit: () => set({ editingAgentId: null, editingAgentName: null, formData: null, originalData: null }),

  openNewWithData: (data: Partial<AgentMetadata>) => {
    set({
      editingAgentId: "__new__",
      editingAgentName: (data.display_name || data.name || "New Agent") as string,
      formData: data,
      originalData: JSON.parse(JSON.stringify(data)),
      loading: false,
    });
  },

  updateField: (key, value) => {
    const { formData } = get();
    if (formData) {
      set({ formData: { ...formData, [key]: value } });
    }
  },

  setSaving: (saving) => set({ saving }),
}));
