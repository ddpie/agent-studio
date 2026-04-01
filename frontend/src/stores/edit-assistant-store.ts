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
        set({ previewContent: "" });
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id ? { ...m, content: m.content + "\n\n---applying-changes---\n\n" } : m
          ),
        }));
      };

      /** Extract {"__update": {...}} from text, properly handling JSON string escaping.
       *  Braces inside JSON string values (e.g., Python code) are ignored. */
      const tryExtractUpdate = (): boolean => {
        let found = false;

        while (true) {
          const marker = '{"__update"';
          const idx = fullText.indexOf(marker);

          if (idx === -1) {
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

          // Proper JSON-aware brace matching: skip braces inside strings
          let depth = 0;
          let inString = false;
          let escape = false;
          let endIdx = -1;

          for (let i = idx; i < fullText.length; i++) {
            const ch = fullText[i];
            if (escape) { escape = false; continue; }
            if (ch === "\\") { escape = true; continue; }
            if (ch === '"') { inString = !inString; continue; }
            if (inString) continue;
            if (ch === "{") depth++;
            else if (ch === "}") {
              depth--;
              if (depth === 0) { endIdx = i + 1; break; }
            }
          }

          if (endIdx === -1) return found; // Not complete yet

          const jsonStr = fullText.slice(idx, endIdx);
          try {
            const parsed = JSON.parse(jsonStr);
            if (parsed.__update && typeof parsed.__update === "object") {
              onUpdate(parsed.__update);
              const fields = Object.keys(parsed.__update);

              pendingText = pendingText.replace(jsonStr, "");

              set((s) => ({
                messages: s.messages.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, content: m.content.replace("\n\n---applying-changes---\n\n", "") + `\n\n---updated:${fields.join(",")}---\n\n` }
                    : m
                ),
              }));

              fullText = fullText.slice(0, idx) + fullText.slice(endIdx);
              showedGenerating = false;
              found = true;
              set({ previewContent: null });
              continue;
            }
          } catch {
            // JSON.parse failed — might still be incomplete despite balanced braces
            // (e.g., truncated string value). Keep holding.
            return found;
          }
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

          // Update preview content when holding
          if (holdFlush) {
            set({ previewContent: pendingText });
          }

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

      // Final attempt: try to extract __update from complete fullText
      // Uses same JSON-aware string/escape tracking as tryExtractUpdate
      if (holdFlush && fullText.includes('"__update"')) {
        const marker = '{"__update"';
        const idx = fullText.indexOf(marker);
        if (idx !== -1) {
          let depth = 0;
          let inStr = false;
          let esc = false;
          let endIdx = -1;
          for (let i = idx; i < fullText.length; i++) {
            const ch = fullText[i];
            if (esc) { esc = false; continue; }
            if (ch === "\\") { esc = true; continue; }
            if (ch === '"') { inStr = !inStr; continue; }
            if (inStr) continue;
            if (ch === "{") depth++;
            else if (ch === "}") { depth--; if (depth === 0) { endIdx = i + 1; break; } }
          }
          if (endIdx !== -1) {
            try {
              const parsed = JSON.parse(fullText.slice(idx, endIdx));
              if (parsed.__update) {
                onUpdate(parsed.__update);
                const fields = Object.keys(parsed.__update);
                // Replace applying marker with updated
                set((s) => ({
                  messages: s.messages.map((m) =>
                    m.id === assistantMsg.id
                      ? { ...m, content: m.content.replace(/\n\n---applying-changes---\n\n/g, "") + `\n\n---updated:${fields.join(",")}---\n\n` }
                      : m
                  ),
                }));
                // Flush remaining text after JSON
                const afterJson = fullText.slice(endIdx).trim();
                if (afterJson) {
                  set((s) => ({
                    messages: s.messages.map((m) =>
                      m.id === assistantMsg.id ? { ...m, content: m.content + afterJson } : m
                    ),
                  }));
                }
                holdFlush = false;
                pendingText = "";
              }
            } catch { /* still can't parse */ }
          }
        }
      }

      // Force cleanup: remove any leftover applying-changes markers
      if (holdFlush) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: m.content.replace(/\n\n---applying-changes---\n\n/g, "\n\n---updated:changes applied---\n\n") }
              : m
          ),
        }));
      }

      // Flush any remaining non-JSON text
      holdFlush = false;
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
