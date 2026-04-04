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
    fileContext: { path: string; content: string; allFiles: string[] },
    onFileUpdate: (newContent: string) => void,
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
- Edit file content (add, modify, remove sections)
- Fix syntax errors in Python/JSON/YAML
- Translate content between languages
- Improve descriptions, documentation, and code quality
- Explain code logic and suggest improvements

## Current File
- Path: ${fileContext.path}
- All files in this skill: ${fileContext.allFiles.join(", ") || "SKILL.md only"}

## File Content
\`\`\`
${fileContext.content}
\`\`\`

## User Request
${content}

## Output Format
When the user asks you to modify the file, output the COMPLETE updated file content wrapped in a single code block:
\`\`\`__file_update
(entire file content here — every line, not just changes)
\`\`\`

Then add 1-2 sentences explaining what you changed.

When the user asks a question or for advice (not a modification), respond with text only — no code block.

## Anti-Patterns

WRONG (partial output — destroys the rest of the file):
\`\`\`
## New Section
Added content here.
\`\`\`

RIGHT (complete file — preserves everything):
\`\`\`
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
- NEVER output multiple __file_update blocks. Only ONE per response.
- NEVER modify files other than the current file. If the user asks to change a different file, tell them to switch to that file first.
- Respond in the SAME LANGUAGE the user uses. If the user writes in Chinese, respond in Chinese.
- For SKILL.md: preserve all valid YAML frontmatter fields (name, description, type, source, user-invocable, files).
- For Python files: ensure valid syntax, include docstrings and type hints.
- Be concise and professional.

## Recognize Your Excuses
You may be tempted to take shortcuts. Recognize these:
- "The file is long, I'll just show the changed part" — NO. Output the COMPLETE file. The frontend replaces the entire file with your output. Partial output = data loss.
- "I'll describe the changes instead of outputting code" — If the user asked for a modification, you MUST output the __file_update block. Descriptions alone don't apply changes.
- "The frontmatter looks fine, I'll skip it" — ALWAYS include frontmatter in SKILL.md updates. Missing frontmatter breaks the skill.`;

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

      // Extract __file_update block
      const updateMatch = fullText.match(/```__file_update\n([\s\S]*?)```/);
      if (updateMatch) {
        const newContent = updateMatch[1].trimEnd();
        onFileUpdate(newContent);

        // Clean the message: remove the code block, add update indicator
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantMsg.id
              ? {
                  ...m,
                  content: m.content
                    .replace(/```__file_update\n[\s\S]*?```/, "")
                    .replace(/\n{3,}/g, "\n\n")
                    .trim() + `\n\n---file-updated:${fileContext.path}---\n\n`,
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
