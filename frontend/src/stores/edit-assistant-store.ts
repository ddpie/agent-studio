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
    const toolDefs = (formContext.tool_definitions as string || "").trim();
    // Extract tool names list for clarity
    const toolNamesList = toolDefs
      ? toolDefs.match(/def\s+(\w+)\s*\(/g)?.map(m => m.replace(/def\s+/, "").replace(/\s*\(/, "")).join(", ") || ""
      : "";
    const allToolNames = (formContext.tool_names as string || "").split(",").map((t: string) => t.trim()).filter(Boolean);
    const localFuncs = new Set(toolNamesList.split(", ").filter(Boolean));
    const builtInTools = allToolNames.filter((n: string) => !localFuncs.has(n));
    const localTools = allToolNames.filter((n: string) => localFuncs.has(n));
    const contextPrompt = `## Role
You are an AI assistant that helps users edit agent configurations. You modify agent fields (system_prompt, tool_definitions, etc.) through structured JSON updates.

## Current Agent Config
- Name: ${formContext.name || ""}
- Display Name: ${formContext.display_name || ""}
- Description: ${formContext.description || ""}
- Template: ${formContext.template_id || ""}
- Welcome Message: ${formContext.welcome_message || ""}
- Suggestions: ${Array.isArray(formContext.suggestions) ? formContext.suggestions.join(", ") : formContext.suggestions || ""}
- Supports Images: ${formContext.supports_images || false}
- All registered tools: [${allToolNames.join(", ")}]
- Built-in tools (pre-installed, NO code in tool_definitions): [${builtInTools.join(", ") || "none"}]
- Local tools (defined in tool_definitions): [${localTools.join(", ") || "none"}]

## Current System Prompt
${(formContext.system_prompt as string || "(empty)")}

${toolDefs ? `## Current Tool Code (source of truth)\n\`\`\`python\n${toolDefs}\n\`\`\`` : "## Tools\nNo tools defined yet."}

## User Request
${content}

## Output Format
When the user asks for a plan, approach, or opinion (e.g., "怎么做", "你打算", "你觉得", "how would you", "what's your plan"), respond with ONLY text explanation. Do NOT output any __update JSON block. Wait for the user to confirm before making changes.

When the user gives a clear instruction to change something (e.g., "改一下", "优化", "添加", "add", "fix", "update"), output EXACTLY one JSON block, then 1-2 sentences explaining what you changed:
\`\`\`json
{"__update": {"field_name": "new_value", ...}}
\`\`\`

WRONG: {"system_prompt": "..."} (missing __update wrapper)
CORRECT: {"__update": {"system_prompt": "...", "tool_names": "..."}}

## Tool Update Rules

### Priority: Built-in tools first
Before creating any new tool, check if a built-in tool already covers the need:
- Built-in tools: [${builtInTools.join(", ") || "none"}]
- If a built-in tool exists for the use case, recommend it and explain how to use it.
- Only create a custom tool when built-in tools genuinely cannot meet the requirement.

### Adding a new tool — Guided Requirement Collection
Do NOT immediately write code. Instead, follow this flow:

Step 1: Confirm no built-in tool fits. If one does, suggest it.
Step 2: Ask the user to choose from options to clarify requirements:
  - "What type of tool do you need?"
    a) Data processing (parse, transform, aggregate)
    b) External API call (HTTP request to a service)
    c) AWS resource operation (S3, DynamoDB, etc.)
    d) Visualization / formatting (charts, tables, reports)
    e) Other (describe briefly)
  - "What input does it take?" (give 2-3 examples based on the type)
  - "What output format?" (e.g., plain text, JSON, HTML/SVG, markdown table)
Step 3: Summarize the spec and ask for confirmation before writing code.
Step 4: Generate the tool code with:
  - Clear docstring with Args/Returns and a usage example
  - Input validation and error handling
  - Edge cases (empty data, wrong types, missing fields)
  - For visualization tools: use the built-in generate_chart as reference pattern

### Modifying a tool
FIRST ask which tool. List: [${toolNamesList}]. Do NOT guess.

### Deleting a tool
NEVER guess which tool to delete. ALWAYS list ALL current tools and ask the user to confirm:
"Current tools: [list each tool with a number]. Which one do you want to delete? (reply with the number or name)"
Only delete the EXACT tool the user confirms. NEVER delete additional tools.

### tool_names
Always output the COMPLETE list (existing + new). ONLY include @tool decorated functions — NEVER include private helpers (def _xxx).

### Code output
a. ADDING: output ONLY the new @tool function. Frontend auto-merges by function name.
b. MODIFYING: output ONLY the changed @tool function.

## System Prompt Modification Modes

### Mode A: Tool changes (user adds/removes/modifies tools)
- ONLY APPEND a "## Tool Usage" section at the END.
- Do NOT touch existing content.

### Mode B: User explicitly asks to optimize/rewrite/improve the system prompt
Apply best practices:
1. Structure with ## headers: Role, Capabilities, Tool Usage, Constraints, Output Format
2. For EACH tool, add specific usage guidance ("When user asks X, use tool Y")
3. Add constraints with "NEVER" for critical rules
4. Add WRONG/CORRECT examples for common mistakes
5. Add rationalization preemption ("You may want to skip tool calls. Recognize: 'I can answer from memory' — call the tool instead.")
6. Add recovery gates (what to do when a tool call fails)

### Mode C: Auto-fix (message starts with "## Auto-Fix Task")
Fix ALL listed validation issues. Rules:
- For system_prompt: APPEND improvements at the end. Do NOT rewrite from scratch.
- For tool_definitions: only output changed tools.
- For tool_names: set to ONLY functions with @tool decorator. NEVER include private helper functions (starting with _). Example: if code has "@tool def smart_svg_chart" and "def _create_bar_chart", tool_names should be "smart_svg_chart" only.
- Fix prompt review warnings by adding missing sections/content, not by rewriting.
- Be precise and minimal — fix only what's flagged.

## Constraints
- NEVER use emojis in any generated content. Non-negotiable.
- NEVER translate existing content unless explicitly asked.
- NEVER remove or restructure existing content in Mode A or Mode C.
- NEVER recreate or rewrite built-in tools from scratch without user confirmation. Built-in tools have been copied into tool_definitions as local code — users can see and edit them. If the user asks about a tool, explain how it works. If they want to modify it, help them edit the existing code.
- NEVER remove tools from tool_names unless the user explicitly asks to remove them.
- Keep the SAME LANGUAGE as the existing content in the field being modified.
- Include ALL changed fields in a SINGLE __update block.
- Keep tool code concise — clear docstrings, type hints, error handling.
- Valid fields: name, display_name, description, system_prompt, tool_definitions, tool_names, welcome_message, suggestions, template_id, supports_images
- Respond in the same language the user uses.
- Be professional and concise.`;

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
      // Strip markdown code fences that may wrap the JSON
      const cleanedText = fullText.replace(/```(?:json)?\s*/g, "").replace(/```/g, "");
      // Match with optional whitespace: { "__update" or {"__update"
      const updateMatch = cleanedText.match(/\{\s*"__update"/);
      const updateIdx = updateMatch ? updateMatch.index! : -1;
      if (updateIdx !== -1) {
        // JSON-aware brace matching
        let depth = 0, inStr = false, esc = false, endIdx = -1;
        for (let i = updateIdx; i < cleanedText.length; i++) {
          const ch = cleanedText[i];
          if (esc) { esc = false; continue; }
          if (ch === "\\") { esc = true; continue; }
          if (ch === '"') { inStr = !inStr; continue; }
          if (inStr) continue;
          if (ch === "{") depth++;
          else if (ch === "}") { depth--; if (depth === 0) { endIdx = i + 1; break; } }
        }
        if (endIdx !== -1) {
          const jsonStr = cleanedText.slice(updateIdx, endIdx);
          try {
            const parsed = JSON.parse(jsonStr);
            if (parsed.__update && typeof parsed.__update === "object") {
              onUpdate(parsed.__update);
              const fields = Object.keys(parsed.__update);
              // Clean the message: remove the JSON block and code fences, add update indicator
              // Remove from original fullText: find the JSON region (may include fences)
              const jsonInOriginal = fullText.indexOf(jsonStr) !== -1 ? jsonStr : "";
              const fencePattern = /```(?:json)?\s*\{[\s\S]*?\}\s*```/;
              const fenceMatch = fullText.match(fencePattern);
              const removeStr = fenceMatch ? fenceMatch[0] : jsonInOriginal;
              set((s) => ({
                messages: s.messages.map((m) =>
                  m.id === assistantMsg.id
                    ? { ...m, content: (removeStr ? m.content.replace(removeStr, "") : m.content).replace(/\n{3,}/g, "\n\n").trim() + `\n\n---updated:${fields.join(",")}---\n\n` }
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
