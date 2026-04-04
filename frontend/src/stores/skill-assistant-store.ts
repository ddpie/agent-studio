/**
 * Skill Assistant Store — AI chat for editing skill files via natural language.
 * Simpler than edit-assistant-store: operates on file content, not form fields.
 */
import { create } from "zustand";
import { readJsonFromS3, writeJsonToS3 } from "../lib/s3-storage";
import { invokeMetaAgent } from "../lib/agentcore-client";

export interface SkillAssistantMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface SkillAssistantState {
  skillId: string | null;
  messages: SkillAssistantMessage[];
  isStreaming: boolean;
  loading: boolean;
  panelOpen: boolean;
  selectedModelId: string | null;

  openPanel: (skillId: string) => void;
  closePanel: () => void;
  togglePanel: () => void;
  setModel: (modelId: string) => void;
  loadHistory: (skillId: string) => Promise<void>;
  sendMessage: (
    content: string,
    fileContext: { path: string; content: string; allFiles: string[]; getFileContent: (path: string) => string | null },
    onFileUpdate: (path: string, newContent: string) => void,
  ) => Promise<void>;
  cancelStreaming: () => void;
  clearHistory: () => void;
}

const S3_KEY = (skillId: string) => `skill-assistant/${skillId}/history.json`;

let _abortController: AbortController | null = null;

export const useSkillAssistantStore = create<SkillAssistantState>((set, get) => ({
  skillId: null,
  messages: [],
  isStreaming: false,
  loading: false,
  panelOpen: false,
  selectedModelId: null,

  openPanel: (skillId: string) => {
    const current = get().skillId;
    set({ panelOpen: true });
    if (current !== skillId) {
      set({ skillId, messages: [], loading: true });
      get().loadHistory(skillId);
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

  loadHistory: async (skillId: string) => {
    set({ loading: true });
    const data = await readJsonFromS3<SkillAssistantMessage[]>(S3_KEY(skillId));
    if (get().skillId === skillId) {
      set({ messages: data || [], loading: false });
    }
  },

  sendMessage: async (content, fileContext, onFileUpdate) => {
    _abortController = new AbortController();
    const signal = _abortController.signal;

    const userMsg: SkillAssistantMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      timestamp: Date.now(),
    };
    const assistantMsg: SkillAssistantMessage = {
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
You are an AI assistant that helps users edit skill files in Agent Studio. Skills follow the AgentSkills.io format (SKILL.md with YAML frontmatter).

## Capabilities
- Edit ANY file in the skill (not just the currently open file)
- Create new files within the skill
- Fix syntax errors in Python/JSON/YAML
- Translate content between languages
- Improve descriptions, documentation, and code quality
- Explain code logic and suggest improvements

## Current File
- Path: ${fileContext.path}
- Content:
\`\`\`
${fileContext.content}
\`\`\`

## All Files in This Skill
${fileContext.allFiles.map(f => `- ${f}`).join("\\n")}

## User Request
${content}

## Modification Workflow

### Step 1: Plan (ALWAYS do this first)
When the user asks for a modification, FIRST describe what you plan to change:
- Which file(s) will be modified
- What changes will be made to each file
- Ask: "Shall I proceed with these changes?"

### Step 2: Execute (only after user confirms)
After the user confirms (e.g., "yes", "go ahead", "do it", "好的", "做吧"), output the file updates.

EXCEPTION: If the user gives a very specific, unambiguous instruction (e.g., "add a comment on line 5"), you may skip the plan and directly output the update.

## Output Format
For each file you want to update, output a code block with the target path:
\`\`\`__file_update:PATH
(entire file content here — every line, not just changes)
\`\`\`

You can update MULTIPLE files in a single response. Each file gets its own block:
\`\`\`__file_update:SKILL.md
(complete SKILL.md content)
\`\`\`

\`\`\`__file_update:scripts/clean_csv.py
(complete script content)
\`\`\`

After the code blocks, add 1-2 sentences explaining what you changed.

When the user asks a question or for advice (not a modification), respond with text only — no code blocks.

## Reading Other Files
If you need to see a file that is not the current file, tell the user to switch to it, OR if the file content was provided in the conversation history, use that.

## Anti-Patterns

WRONG (partial output — destroys the rest of the file):
\`\`\`
## New Section
Added content here.
\`\`\`

RIGHT (complete file — preserves everything):
\`\`\`__file_update:SKILL.md
---
name: "my-skill"
description: "..."
type: "prompt"
---

# Original Title

Original content preserved.

## New Section
Added content here.
\`\`\`

## Constraints
- NEVER output a __file_update for SKILL.md without valid YAML frontmatter (---\\nname: ...\\n---). Missing frontmatter will break the skill.
- For multi-file changes, ALWAYS describe the plan first and wait for confirmation.
- Respond in the SAME LANGUAGE the user uses.
- For SKILL.md: preserve all valid YAML frontmatter fields (name, description, type, source, user-invocable, files).
- For Python files: ensure valid syntax, include docstrings and type hints.
- Be concise and professional.

## Recognize Your Excuses
You may be tempted to take shortcuts. Recognize these:
- "The file is long, I'll just show the changed part" — NO. Output the COMPLETE file. The frontend replaces the entire file with your output. Partial output = data loss.
- "I'll describe the changes instead of outputting code" — If the user confirmed changes, you MUST output the __file_update block(s).
- "The frontmatter looks fine, I'll skip it" — ALWAYS include frontmatter in SKILL.md updates.
- "I'll make all the changes without asking" — For multi-file changes, ALWAYS plan first.`;

    const history = get()
      .messages.filter((m) => m.id !== assistantMsg.id && m.content)
      .map(({ role, content: c }) => ({
        role: role as "user" | "assistant",
        content: c.length > 2000 ? c.slice(0, 2000) + "..." : c,
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

      // Extract __file_update blocks (supports __file_update:PATH and legacy __file_update)
      const updateRegex = /```__file_update(?::([^\n]*))?\n([\s\S]*?)```/g;
      let match;
      const updatedPaths: string[] = [];
      let cleanedContent = fullText;

      while ((match = updateRegex.exec(fullText)) !== null) {
        const targetPath = match[1]?.trim() || fileContext.path;
        const newContent = match[2].trimEnd();
        onFileUpdate(targetPath, newContent);
        updatedPaths.push(targetPath);
        cleanedContent = cleanedContent.replace(match[0], "");
      }

      if (updatedPaths.length > 0) {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? {
                  ...m,
                  content: cleanedContent
                    .replace(/\n{3,}/g, "\n\n")
                    .trim() + `\n\n---file-updated:${updatedPaths.join(", ")}---\n\n`,
                }
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
      const { skillId, messages } = get();
      if (skillId) {
        writeJsonToS3(S3_KEY(skillId), messages);
      }
    }
  },

  clearHistory: () => {
    const { skillId } = get();
    set({ messages: [] });
    if (skillId) {
      writeJsonToS3(S3_KEY(skillId), []);
    }
  },
}));
