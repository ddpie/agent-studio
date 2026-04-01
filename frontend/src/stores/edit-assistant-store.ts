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
- System Prompt:
${formContext.system_prompt || "(empty)"}
- Tool Definitions:
${formContext.tool_definitions || "(none)"}
- Tool Names: ${formContext.tool_names || ""}
- Welcome Message: ${formContext.welcome_message || ""}
- Suggestions: ${Array.isArray(formContext.suggestions) ? formContext.suggestions.join(", ") : ""}
- Supports Images: ${formContext.supports_images || false}

User request: ${content}

IMPORTANT: If you need to modify any config field, you MUST output the JSON block FIRST, before any explanation text.
Output EXACTLY one JSON block per response on its own line:
{"__update": {"field_name": "new_value"}}
If updating multiple fields, include ALL of them in a SINGLE __update block:
{"__update": {"tool_definitions": "...", "tool_names": "...", "suggestions": [...]}}
Valid field names: name, display_name, description, system_prompt, tool_definitions, tool_names, welcome_message, suggestions, template_id, supports_images
After the JSON block, briefly explain what you changed in 1-2 sentences. Do not use emojis. Do not repeat the code in your explanation.`;

    // Build history for context
    const history = get()
      .messages.filter((m) => m.id !== assistantMsg.id && m.content)
      .map(({ role, content: c }) => ({ role: role as "user" | "assistant", content: c }));

    try {
      const stream = invokeMetaAgent(contextPrompt, history, undefined, undefined, undefined, get().selectedModelId || undefined);

      let pendingText = "";
      let flushTimer: ReturnType<typeof setTimeout> | null = null;
      let fullText = ""; // Accumulate full response to detect __update
      let holdFlush = false; // Hold flushing when we detect partial __update
      let showedGenerating = false;

      const flushPending = () => {
        if (!pendingText || holdFlush) return;
        const text = pendingText;
        pendingText = "";
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + text } : m
          ),
        }));
      };

      const showGenerating = () => {
        if (showedGenerating) return;
        showedGenerating = true;
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + "\n> Applying changes..." } : m
          ),
        }));
      };

      /** Try to extract ALL {"__update": {...}} from accumulated text using brace balancing */
      const tryExtractUpdate = (): boolean => {
        let found = false;

        // Loop to handle multiple consecutive __update blocks
        while (true) {
          const marker = '{"__update"';
          const idx = fullText.indexOf(marker);

          // Hold if pending text contains { that could be start of __update
          if (idx === -1) {
            // Check if there's a partial JSON starting
            const pendingTrimmed = pendingText.trimStart();
            if (pendingTrimmed.startsWith("{") || pendingTrimmed.startsWith('{"')) {
              holdFlush = true;
              showGenerating();
              return found;
            }
            holdFlush = false;
            return found;
          }

          holdFlush = true;
          showGenerating();

          // Find balanced braces starting from idx
          let depth = 0;
          let endIdx = -1;
          for (let i = idx; i < fullText.length; i++) {
            if (fullText[i] === "{") depth++;
            else if (fullText[i] === "}") {
              depth--;
              if (depth === 0) { endIdx = i + 1; break; }
            }
          }
          if (endIdx === -1) return found; // Not complete yet, keep holding

          const jsonStr = fullText.slice(idx, endIdx);
          try {
            const parsed = JSON.parse(jsonStr);
            if (parsed.__update && typeof parsed.__update === "object") {
              onUpdate(parsed.__update);

              const fields = Object.keys(parsed.__update);

              // Remove JSON from pendingText
              pendingText = pendingText.replace(jsonStr, "");

              // Remove "Applying changes..." and add result
              set((s) => ({
                messages: s.messages.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, content: m.content.replace("\n> Applying changes...", "") + `\n> Updated: ${fields.join(", ")}\n` }
                    : m
                ),
              }));

              // Clean fullText and continue looking for more
              fullText = fullText.slice(0, idx) + fullText.slice(endIdx);
              showedGenerating = false;
              found = true;
              continue; // Check for more __update blocks
            }
          } catch { /* incomplete JSON */ }
          return found;
        }
      };

      for await (const chunk of stream) {
        if (signal.aborted) break;

        // Strip tool markers
        const toolRe = /\{"__tool"[^}]*\}/g;
        const cleaned = chunk.replace(toolRe, "");

        if (cleaned) {
          fullText += cleaned;
          pendingText += cleaned;

          // Check for __update — may extract multiple
          tryExtractUpdate();

          // Only flush if not holding (i.e., no pending JSON detection)
          if (!holdFlush) {
            if (!flushTimer) {
              flushTimer = setTimeout(() => {
                flushTimer = null;
                // Re-check before flushing in case new chunks arrived
                tryExtractUpdate();
                if (!holdFlush) flushPending();
              }, 100);
            }
          }
        }
      }

      if (flushTimer) clearTimeout(flushTimer);
      flushPending();
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
      set({ isStreaming: false });

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
