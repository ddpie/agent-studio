/**
 * Edit Assistant Store — manages AI assistant chat history per agent.
 * Persists to S3 for cross-browser access.
 */
import { create } from "zustand";
import { fetchAgentHistory, putAgentHistory, getStorage, putStorage } from "../lib/api-client";
import { invokeMetaAgent } from "../lib/agentcore-client";
import { useUISettings } from "./ui-settings-store";
import { useAgentEditStore } from "./agent-edit-store";

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
  previewContent: string | null;

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

const DRAFT_STORAGE_KEY = (draftId: string) => `drafts/${draftId}/history.json`;

async function loadHistoryData(agentId: string): Promise<AssistantMessage[] | null> {
  if (agentId.startsWith("draft-")) {
    return getStorage<AssistantMessage[]>(DRAFT_STORAGE_KEY(agentId));
  }
  return fetchAgentHistory(agentId) as Promise<AssistantMessage[] | null>;
}

async function saveHistoryData(agentId: string, messages: AssistantMessage[]): Promise<void> {
  if (agentId.startsWith("draft-")) {
    await putStorage(DRAFT_STORAGE_KEY(agentId), messages);
  } else {
    await putAgentHistory(agentId, messages);
  }
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
    const data = await loadHistoryData(agentId);
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

${(() => {
  const allSkills = (formContext.skills as Array<{ id: string; name: string; description: string; files: string[] }>) || [];
  const editStore = useAgentEditStore.getState();
  const editingSkillId = editStore.editingSkillId;
  const skills = editingSkillId ? allSkills.filter(s => s.id === editingSkillId) : allSkills;
  if (skills.length === 0) return "";
  const pendingFiles = editStore.pendingSkillFiles || {};
  const originalFiles = editStore.originalSkillFiles || {};

  // Surface dirty in-editor edits so the assistant doesn't read a stale
  // S3 version via read_skill_file and then clobber unsaved work. A file
  // is "dirty" when its pending content differs from the original we
  // captured on first load. The assistant is told to treat these as the
  // source of truth and NOT to call read_skill_file for them.
  const MAX_DIRTY_BYTES = 20000;
  const collectDirty = (skillId: string): Array<{ path: string; content: string; truncated: boolean }> => {
    const pending = pendingFiles[skillId] || {};
    const original = originalFiles[skillId] || {};
    const out: Array<{ path: string; content: string; truncated: boolean }> = [];
    for (const [path, content] of Object.entries(pending)) {
      if (typeof content !== "string") continue;
      if (original[path] === content) continue; // clean — same as S3 baseline
      const truncated = content.length > MAX_DIRTY_BYTES;
      out.push({
        path,
        content: truncated ? content.slice(0, MAX_DIRTY_BYTES) + `\n\n... [truncated ${content.length - MAX_DIRTY_BYTES} chars]` : content,
        truncated,
      });
    }
    return out;
  };

  const renderDirty = (skillId: string): string => {
    const dirty = collectDirty(skillId);
    if (dirty.length === 0) return "";
    const blocks = dirty.map(({ path, content, truncated }) =>
      `### ${path}${truncated ? " (truncated)" : ""}\n\`\`\`\n${content}\n\`\`\``
    );
    return `\n\n  **Unsaved edits in the editor (override S3 for these paths):**\n\n${blocks.join("\n\n")}`;
  };

  return `## Bound Skills${editingSkillId ? " (currently editing)" : ""}
${skills.map(s => `- ${s.name} (id=${s.id}): ${s.description}\n  files: ${s.files.join(", ")}${renderDirty(s.id)}`).join("\n")}

Before rewriting or reviewing a skill file, use read_skill_file(skill_id, path) to fetch its content (and list_skill_files(skill_id) first if you need the full tree). Don't guess file content you haven't read. Pull only the files relevant to the ask — for "optimize SKILL.md", read SKILL.md plus any INDEX / layout / helper files that SKILL.md references.

**Unsaved-edits rule:** If a file appears under "Unsaved edits in the editor" above, use that inline content as the source of truth — do NOT call read_skill_file for it. The S3 copy is stale compared to what the user is currently editing, and reading it would cause you to overwrite their in-progress changes.

When the user asks to modify a skill file, write the new content with __field_value:

\`\`\`\`__field_value:skill:{skillId}:{filePath}
complete new file content
\`\`\`\`
`;
})()}

## User Request
${content}

## Output Format
When the user asks for a plan, approach, or opinion (e.g., "怎么做", "你打算", "你觉得", "how would you", "what's your plan"), respond with ONLY text explanation. Do NOT output any __field_value block. Wait for the user to confirm before making changes.

When the user gives a clear instruction to change something, output the COMPLETE new value using __field_value:

\`\`\`\`__field_value:FIELD_NAME
complete new content here (no escaping needed, write as-is, triple backticks are safe inside)
\`\`\`\`

### Rules
- ALWAYS use __field_value. No other format.
- Output the COMPLETE new value of the field — not a diff, not a partial edit.
- You can output multiple __field_value blocks for different fields in one response.
- NEVER explain your format choice to the user. Just output the block directly.

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
Fix ALL listed validation issues immediately. Do NOT ask for confirmation. Rules:
- Prefer __field_value for large changes.
- For tool_definitions: only output changed tools.
- Fix prompt review warnings by adding missing sections/content, not by rewriting.
- Be precise and minimal — fix only what's flagged.

## Constraints
- NEVER use emojis in any generated content. Non-negotiable.
- NEVER translate existing content unless explicitly asked.
- NEVER remove or restructure existing content in Mode A or Mode C.
- NEVER recreate or rewrite built-in tools from scratch without user confirmation. Built-in tools have been copied into tool_definitions as local code — users can see and edit them. If the user asks about a tool, explain how it works. If they want to modify it, help them edit the existing code.
- NEVER remove tools from tool_names unless the user explicitly asks to remove them.
- NEVER delete a tool without listing ALL current tools and getting explicit confirmation on which one to delete.
- Keep the SAME LANGUAGE as the existing content in the field being modified.

## Recognize Your Excuses
You may be tempted to take shortcuts. Recognize these:
- "The user wants a tool, I'll write it immediately" — check built-in tools first. ALWAYS.
- "I know what tool to delete" — NEVER guess. List all tools and ask.
- "The prompt is fine, no need to add Tool Usage guidance" — if there are tools, EVERY tool needs "When user asks X, use tool Y" guidance.
- "I'll rewrite the whole prompt to make it better" — in Mode A and C, ONLY append. Do NOT restructure.

## Skills
Sub-agents now support skills (AgentSkills.io format). Skills are loaded dynamically at runtime via load_skill(name).
When optimizing a system prompt (Mode B), mention that the agent can use load_skill to access specialized instructions.
- Keep tool code concise — clear docstrings, type hints, error handling.
- Valid fields: name, display_name, description, system_prompt, tool_definitions, welcome_message, suggestions, template_id, supports_images
- tool_names is auto-computed from tool_definitions — do NOT set it manually.
- Respond in the same language the user uses.
- Be professional and concise.`;

    // Inject language instruction based on user settings
    const lang = useUISettings.getState().language;
    const LANG_INSTRUCTIONS: Record<string, string> = {
      zh: "\n\n## Language\n请用中文回复。所有解释、计划确认、错误提示都用中文。代码和技术标识符保持英文。",
      en: "\n\n## Language\nRespond in English. All explanations, plan confirmations, and error messages in English. Keep code and technical identifiers as-is.",
    };
    const finalPrompt = contextPrompt + (LANG_INSTRUCTIONS[lang] ?? LANG_INSTRUCTIONS.en);

    // Build history (exclude tool_definitions from context to save tokens)
    const history = get()
      .messages.filter((m) => m.id !== assistantMsg.id && m.content)
      .map(({ role, content: c }) => ({
        role: role as "user" | "assistant",
        content: c.length > 1000 ? c.slice(0, 1000) + "..." : c,
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

      // Post-stream: extract __field_value blocks (whole-file replacement)
      const editedFields: string[] = [];
      let processedText = fullText;
      const fieldValues: Record<string, string> = {};

      const ALLOWED_FIELDS = new Set(["name", "display_name", "description", "system_prompt", "tool_definitions", "welcome_message", "suggestions", "template_id", "supports_images"]);

      {
        const lines = fullText.split("\n");
        let i = 0;
        while (i < lines.length) {
          // Match 4-backtick fence: ````__field_value:FIELD_NAME
          const openMatch = lines[i].match(/^````__field_value:(.+)/);
          if (openMatch) {
            const fieldName = openMatch[1].trim();
            const startLine = i;
            i++;
            // Find closing ```` (exactly 4 backticks on its own line)
            while (i < lines.length && !lines[i].match(/^````\s*$/)) {
              i++;
            }
            if (i >= lines.length) {
              console.warn(`[field_value] unclosed 4-backtick fence for "${fieldName}", skipping`);
              i++;
              continue;
            }
            if (!ALLOWED_FIELDS.has(fieldName) && !fieldName.startsWith("skill:")) {
              console.warn(`[field_value] unknown field "${fieldName}", skipping`);
              i++;
              continue;
            }
            const endLine = i;
            const content = lines.slice(startLine + 1, endLine).join("\n");
            const raw = lines.slice(startLine, endLine + 1).join("\n");

            // Handle skill file updates: skill:{skillId}:{filePath}
            if (fieldName.startsWith("skill:")) {
              const parts = fieldName.split(":");
              if (parts.length >= 3) {
                const skillId = parts[1];
                const filePath = parts.slice(2).join(":");
                // Write skill file via pending skill files in agent-edit-store
                const { setPendingSkillFiles, getPendingSkillFiles } = useAgentEditStore.getState();
                const existing = getPendingSkillFiles(skillId) || {};
                setPendingSkillFiles(skillId, { ...existing, [filePath]: content });
                editedFields.push(`skill:${skillId}:${filePath}`);
                processedText = processedText.replace(raw, "");
              }
            } else {
              fieldValues[fieldName] = content;
              onUpdate({ [fieldName]: content });
              editedFields.push(fieldName);
              processedText = processedText.replace(raw, "");
            }
          }
          i++;
        }
      }

      // Post-stream: clean up and show update indicator
      if (editedFields.length > 0) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? { ...m, content: processedText.replace(/\n{3,}/g, "\n\n").trim() + `\n\n---updated:${editedFields.join(",")}---\n\n` }
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
      set({ isStreaming: false, previewContent: null });

      // Persist history (async, non-blocking)
      const { agentId, messages } = get();
      if (agentId) {
        saveHistoryData(agentId, messages);
      }
    }
  },

  clearHistory: () => {
    const { agentId } = get();
    set({ messages: [] });
    if (agentId) {
      saveHistoryData(agentId, []);
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
