/**
 * Edit Assistant Store — manages AI assistant chat history per agent.
 * Persists to S3 for cross-browser access.
 */
import { create } from "zustand";
import { readJsonFromS3, writeJsonToS3 } from "../lib/s3-storage";
import { invokeMetaAgent } from "../lib/agentcore-client";

export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface EditAssistantState {
  agentId: string | null;
  messages: AssistantMessage[];
  isStreaming: boolean;
  loading: boolean;
  panelOpen: boolean;
  selectedModelId: string | null;
  previewContent: string | null; // Raw streaming content during __update hold

  openPanel: (agentId: string) => void;
  closePanel: () => void;
  togglePanel: () => void;
  setModel: (modelId: string) => void;
  loadHistory: (agentId: string) => Promise<void>;
  sendMessage: (
    content: string,
    formContext: Record<string, unknown>,
    onUpdate: (updates: Record<string, unknown>) => void
  ) => Promise<void>;
  regenerateLastMessage: (
    formContext: Record<string, unknown>,
    onUpdate: (updates: Record<string, unknown>) => void
  ) => Promise<void>;
  editAndResend: (
    messageId: string,
    newContent: string,
    formContext: Record<string, unknown>,
    onUpdate: (updates: Record<string, unknown>) => void
  ) => Promise<void>;
  cancelStreaming: () => void;
  clearHistory: () => void;
}

const S3_KEY = (agentId: string) => `agents/${agentId}/assistant-history.json`;
const DRAFT_KEY = (draftId: string) => `drafts/${draftId}/assistant-history.json`;

function storageKey(agentId: string): string {
  return agentId.startsWith("draft-") ? DRAFT_KEY(agentId) : S3_KEY(agentId);
}

let _abortController: AbortController | null = null;

export const useEditAssistantStore = create<EditAssistantState>((set, get) => ({
  agentId: null,
  messages: [],
  isStreaming: false,
  loading: false,
  panelOpen: false,
  selectedModelId: null,
  previewContent: null,

  openPanel: (agentId: string) => {
    const current = get().agentId;
    set({ panelOpen: true });
    if (current !== agentId) {
      set({ agentId, messages: [], loading: true });
      get().loadHistory(agentId);
    }
  },

  closePanel: () => set({ panelOpen: false }),

  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),

  setModel: (modelId: string) => set({ selectedModelId: modelId }),

  cancelStreaming: () => {
    if (_abortController) {
      _abortController.abort();
      _abortController = null;
    }
    set({ isStreaming: false });
  },

  loadHistory: async (agentId: string) => {
    set({ loading: true });
    const data = await readJsonFromS3<AssistantMessage[]>(storageKey(agentId));
    // Only update if still viewing the same agent
    if (get().agentId === agentId) {
      set({ messages: data || [], loading: false });
    }
  },

  sendMessage: async (content, formContext, onUpdate) => {
    _abortController = new AbortController();
    const signal = _abortController.signal;

    const userMsg: AssistantMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      timestamp: Date.now(),
    };

    const assistantMsg: AssistantMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      isStreaming: true,
    }));

    // Build context prompt with current form data
    const contextPrompt = `You are an AI assistant helping edit an agent configuration.
Current agent config:
- Name: ${formContext.name || ""}
- Display Name: ${formContext.display_name || ""}
- Description: ${formContext.description || ""}
- Template: ${formContext.template_id || ""}
- System Prompt (first 500 chars):
${(formContext.system_prompt as string || "").slice(0, 500)}${(formContext.system_prompt as string || "").length > 500 ? "..." : ""}
- Tool Names: ${formContext.tool_names || ""}
- Welcome Message: ${formContext.welcome_message || ""}
- Suggestions: ${Array.isArray(formContext.suggestions) ? formContext.suggestions.join(", ") : formContext.suggestions || ""}
- Supports Images: ${formContext.supports_images || false}

Note: Tool definitions are NOT shown here to save tokens. The user can see them in the editor.

User request: ${content}

RULES:
1. Output the __update JSON block FIRST, before any explanation.
2. Use EXACTLY one JSON block: {"__update": {"field_name": "new_value"}}
3. For tool_definitions: if adding a NEW tool, output ONLY the new @tool function code. If modifying existing tools, output ALL tool code (existing + modified).
4. Include ALL changed fields in a SINGLE __update block.
5. After the JSON, explain in 1-2 sentences. No emojis. No code in explanation.
6. Keep tool code concise — avoid overly long implementations.
7. Valid fields: name, display_name, description, system_prompt, tool_definitions, tool_names, welcome_message, suggestions, template_id, supports_images`;

    // Build history (exclude tool_definitions from context to save tokens)
    const history = get()
      .messages.filter((m) => m.id !== assistantMsg.id && m.content)
      .map(({ role, content: c }) => ({
        role: role as "user" | "assistant",
        content: c.length > 1000 ? c.slice(0, 1000) + "..." : c,
      }));

    try {
      const stream = invokeMetaAgent(contextPrompt, history, undefined, undefined, undefined, get().selectedModelId || undefined);

      let pendingText = "";
      let flushTimer: ReturnType<typeof setTimeout> | null = null;
      let fullText = "";

      const flushPending = () => {
        if (!pendingText) return;
        const text = pendingText;
        pendingText = "";
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + text } : m
          ),
        }));
      };

      for await (const chunk of stream) {
        if (signal.aborted) break;

        // Strip tool markers
        const toolRe = /\{"__tool"[^}]*\}/g;
        const cleaned = chunk.replace(toolRe, "");

        if (cleaned) {
          fullText += cleaned;
          pendingText += cleaned;
          // Update preview content for the preview modal
          set({ previewContent: fullText });
          if (!flushTimer) {
            flushTimer = setTimeout(() => {
              flushTimer = null;
              flushPending();
            }, 50);
          }
        }
      }

      if (flushTimer) clearTimeout(flushTimer);
      flushPending();

      // Post-stream: extract __update JSON from the complete response
      const updateMarker = '{"__update"';
      const updateIdx = fullText.indexOf(updateMarker);
      if (updateIdx !== -1) {
        // JSON-aware brace matching
        let depth = 0, inStr = false, esc = false, endIdx = -1;
        for (let i = updateIdx; i < fullText.length; i++) {
          const ch = fullText[i];
          if (esc) { esc = false; continue; }
          if (ch === "\\") { esc = true; continue; }
          if (ch === '"') { inStr = !inStr; continue; }
          if (inStr) continue;
          if (ch === "{") depth++;
          else if (ch === "}") { depth--; if (depth === 0) { endIdx = i + 1; break; } }
        }
        if (endIdx !== -1) {
          const jsonStr = fullText.slice(updateIdx, endIdx);
          try {
            const parsed = JSON.parse(jsonStr);
            if (parsed.__update && typeof parsed.__update === "object") {
              onUpdate(parsed.__update);
              const fields = Object.keys(parsed.__update);
              // Clean the message: remove JSON, add update indicator
              set((s) => ({
                messages: s.messages.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, content: m.content.replace(jsonStr, "").replace(/\n{3,}/g, "\n\n").trim() + `\n\n---updated:${fields.join(",")}---\n\n` }
                    : m
                ),
              }));
            }
          } catch { /* JSON parse failed */ }
        }
      }
    } catch (err) {
      if (!signal.aborted) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: `Error: ${err instanceof Error ? err.message : "Unknown error"}` }
              : m
          ),
        }));
      }
    } finally {
      _abortController = null;
      set({ isStreaming: false, previewContent: null });

      // Persist to S3 (async, non-blocking)
      const { agentId, messages } = get();
      if (agentId) {
        writeJsonToS3(storageKey(agentId), messages);
      }
    }
  },

  clearHistory: () => {
    const { agentId } = get();
    set({ messages: [] });
    if (agentId) {
      writeJsonToS3(storageKey(agentId), []);
    }
  },

  regenerateLastMessage: async (formContext, onUpdate) => {
    const { messages, isStreaming } = get();
    if (isStreaming) return;

    const lastUserIdx = messages.findLastIndex((m) => m.role === "user");
    if (lastUserIdx === -1) return;

    const content = messages[lastUserIdx].content;
    const trimmed = messages.slice(0, lastUserIdx);
    set({ messages: trimmed });

    await get().sendMessage(content, formContext, onUpdate);
  },

  editAndResend: async (messageId, newContent, formContext, onUpdate) => {
    const { messages, isStreaming } = get();
    if (isStreaming) return;

    const msgIdx = messages.findIndex((m) => m.id === messageId);
    if (msgIdx === -1) return;

    const trimmed = messages.slice(0, msgIdx);
    set({ messages: trimmed });

    await get().sendMessage(newContent, formContext, onUpdate);
  },
}));
