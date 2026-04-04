/**
 * Tool Assistant Store — AI chat for editing tool code via natural language.
 * Modeled after skill-assistant-store.ts but for @tool function editing.
 */
import { create } from "zustand";
import { readJsonFromS3, writeJsonToS3 } from "../lib/s3-storage";
import { invokeMetaAgent } from "../lib/agentcore-client";
import { useUISettings } from "./ui-settings-store";

export interface ToolAssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface ToolAssistantState {
  toolId: string | null;
  messages: ToolAssistantMessage[];
  isStreaming: boolean;
  loading: boolean;
  panelOpen: boolean;
  selectedModelId: string | null;

  openPanel: (toolId: string) => void;
  closePanel: () => void;
  togglePanel: () => void;
  setModel: (modelId: string) => void;
  loadHistory: (toolId: string) => Promise<void>;
  sendMessage: (
    content: string,
    toolContext: { name: string; description: string; category: string; code: string },
    onCodeUpdate: (newCode: string) => void,
  ) => Promise<void>;
  cancelStreaming: () => void;
  clearHistory: () => void;
}

const S3_KEY = (toolId: string) => `agents/tool-assistant/${toolId}/history.json`;

let _abortController: AbortController | null = null;

export const useToolAssistantStore = create<ToolAssistantState>((set, get) => ({
  toolId: null,
  messages: [],
  isStreaming: false,
  loading: false,
  panelOpen: false,
  selectedModelId: null,

  openPanel: (toolId: string) => {
    const current = get().toolId;
    set({ panelOpen: true });
    if (current !== toolId) {
      set({ toolId, messages: [], loading: true });
      get().loadHistory(toolId);
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

  loadHistory: async (toolId: string) => {
    set({ loading: true });
    const data = await readJsonFromS3<ToolAssistantMessage[]>(S3_KEY(toolId));
    if (get().toolId === toolId) {
      set({ messages: data || [], loading: false });
    }
  },

  sendMessage: async (content, toolContext, onCodeUpdate) => {
    _abortController = new AbortController();
    const signal = _abortController.signal;

    const userMsg: ToolAssistantMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      timestamp: Date.now(),
    };
    const assistantMsg: ToolAssistantMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      isStreaming: true,
    }));

    const contextPrompt = `## Role
You are an AI assistant that helps users write and edit Python @tool functions for Agent Studio.

## Current Tool
- Name: ${toolContext.name || "(unnamed)"}
- Description: ${toolContext.description || "(empty)"}
- Category: ${toolContext.category || "custom"}

## Current Code
\`\`\`python
${toolContext.code || "# No code yet"}
\`\`\`

## User Request
${content}

## Modification Workflow

### Step 1: Plan (ALWAYS do this first)
When the user asks for a modification, FIRST describe what you plan to change:
- What the tool will do
- Input parameters and return type
- Key implementation details
- Ask: "Shall I proceed?"

### Step 2: Execute (only after user confirms)
After the user confirms, output the complete tool code.

EXCEPTION: If the user gives a very specific, unambiguous instruction (e.g., "add a timeout parameter"), you may skip the plan and directly output the update.

## Output Format
When outputting updated code, use this exact format:
\`\`\`__tool_update
(complete @tool function code — every line, not just changes)
\`\`\`

After the code block, add 1-2 sentences explaining what you changed.

When the user asks a question or for advice (not a modification), respond with text only — no code blocks.

## Code Requirements
- MUST have @tool decorator (from strands import tool)
- MUST have a docstring with Args/Returns
- MUST have type hints on all parameters and return type
- MUST include error handling (try/except for external calls)
- MUST return a string (all @tool functions return str)
- Keep imports inside the function body (sandbox environment)

## Anti-Patterns

WRONG (partial output — destroys the rest of the function):
\`\`\`
    # new logic here
    return result
\`\`\`

RIGHT (complete function):
\`\`\`__tool_update
@tool
def my_tool(query: str, max_results: int = 5) -> str:
    """Search for information.

    Args:
        query: The search query.
        max_results: Maximum number of results.

    Returns:
        Search results as formatted text.
    """
    try:
        # implementation
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"
\`\`\`

## Constraints
- NEVER output a __tool_update without the @tool decorator. Missing decorator will break the tool.
- ALWAYS output the COMPLETE function. The frontend replaces the entire code with your output. Partial output = data loss.
- Respond in the SAME LANGUAGE the user uses.
- Be concise and professional.

## Recognize Your Excuses
- "The function is long, I'll just show the changed part" — NO. Output the COMPLETE function.
- "I'll describe the changes instead of outputting code" — If the user confirmed changes, you MUST output the __tool_update block.`;

    // Inject language preference
    const lang = useUISettings.getState().language;
    const LANG_INSTRUCTIONS: Record<string, string> = {
      zh: "\n\n## Language\n请用中文回复。所有解释、计划确认和代码注释都用中文。",
      en: "\n\n## Language\nRespond in English. All explanations, plan confirmations, and code comments should be in English.",
    };
    const finalPrompt = contextPrompt + (LANG_INSTRUCTIONS[lang] ?? LANG_INSTRUCTIONS.en);

    const history = get()
      .messages.filter((m) => m.id !== assistantMsg.id && m.content)
      .map(({ role, content: c }) => ({
        role: role as "user" | "assistant",
        content: c.length > 2000 ? c.slice(0, 2000) + "..." : c,
      }));

    try {
      const stream = invokeMetaAgent(finalPrompt, history, undefined, undefined, undefined, get().selectedModelId || undefined);

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
        const toolRe = /\{"__tool"[^}]*\}/g;
        const cleaned = chunk.replace(toolRe, "");
        if (cleaned) {
          fullText += cleaned;
          pendingText += cleaned;
          if (!flushTimer) {
            flushTimer = setTimeout(() => { flushTimer = null; flushPending(); }, 50);
          }
        }
      }

      if (flushTimer) clearTimeout(flushTimer);
      flushPending();

      // Extract __tool_update block
      const updateRegex = /```__tool_update\s*\n([\s\S]*?)```/g;
      const match = updateRegex.exec(fullText);
      if (match) {
        const newCode = match[1].trimEnd();
        onCodeUpdate(newCode);
        // Clean message: remove code block, add indicator
        const cleanedContent = fullText.replace(match[0], "").replace(/\n{3,}/g, "\n\n").trim();
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: cleanedContent + "\n\n---tool-updated---\n\n" }
              : m
          ),
        }));
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
      set({ isStreaming: false });
      const { toolId, messages } = get();
      if (toolId) {
        writeJsonToS3(S3_KEY(toolId), messages).catch(() => {});
      }
    }
  },

  clearHistory: () => {
    const { toolId } = get();
    set({ messages: [] });
    if (toolId) {
      writeJsonToS3(S3_KEY(toolId), []);
    }
  },
}));
