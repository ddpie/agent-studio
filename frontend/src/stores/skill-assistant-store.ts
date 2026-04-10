/**
 * Skill Assistant Store — AI chat for editing skill files via natural language.
 * Simpler than edit-assistant-store: operates on file content, not form fields.
 */
import { create } from "zustand";
import { fetchSkillHistory, putSkillHistory } from "../lib/api-client";
import { invokeMetaAgent } from "../lib/agentcore-client";
import { useUISettings } from "./ui-settings-store";

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
    const data = await fetchSkillHistory(skillId) as SkillAssistantMessage[] | null;
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
You are an AI assistant that helps edit skill files in Agent Studio (AgentSkills.io format: SKILL.md with YAML frontmatter).

## Current File
- Path: ${fileContext.path}
- Lines: ${fileContext.content.split("\\n").length}
\`\`\`
${fileContext.content}
\`\`\`

## All Files in This Skill
${fileContext.allFiles.map(f => {
  const c = f === fileContext.path ? null : fileContext.getFileContent(f);
  return c ? `### ${f} (${c.split("\\n").length} lines)\n\`\`\`\n${c}\n\`\`\`` : `- ${f}`;
}).join("\\n")}

## User Request
${content}

## Workflow
- For non-trivial changes: describe your plan first, wait for user confirmation, then execute.
- For trivial fixes (typo, comment, rename): execute directly.
- For questions or advice: respond with text only, no code blocks.

## Output Format

Use 4 backticks (\`\`\`\`) for all output blocks.

### __file_content (whole file replacement)
\`\`\`\`__file_content:PATH
complete file content
\`\`\`\`
Use when: file < 200 lines, large rewrite, or new file.

### __file_edit (search/replace)
\`\`\`\`__file_edit:PATH
<<<<<<< SEARCH
exact text from file
=======
replacement
>>>>>>> REPLACE
\`\`\`\`
Use when: file >= 200 lines with small changes. SEARCH must match exactly.

After code blocks, briefly explain what changed.

## Rules
- SKILL.md with __file_content MUST include valid YAML frontmatter.
- For Python: ensure valid syntax, docstrings, type hints.
- Multi-file changes: plan first, wait for confirmation.
- NEVER output partial content without __file_content or __file_edit markers.
- Respond in the SAME LANGUAGE the user uses.`;

    // Inject language instruction based on user settings
    const lang = useUISettings.getState().language;
    const LANG_INSTRUCTIONS: Record<string, string> = {
      zh: "\n\n## Language\n请用中文回复。所有解释、计划确认、错误提示都用中文。代码和技术标识符保持英文。",
      en: "\n\n## Language\nRespond in English. All explanations, plan confirmations, and error messages in English. Keep code and technical identifiers as-is.",
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

      // Extract __file_content blocks (4-backtick fence)
      const updatedPaths: string[] = [];
      let cleanedContent = fullText;

      {
        const lines = fullText.split("\n");
        let i = 0;
        while (i < lines.length) {
          const openMatch = lines[i].match(/^````__file_content:(.+)/);
          if (openMatch) {
            const targetPath = openMatch[1].trim() || fileContext.path;
            const startLine = i;
            i++;
            while (i < lines.length && !lines[i].match(/^````\s*$/)) {
              i++;
            }
            if (i < lines.length) {
              const newContent = lines.slice(startLine + 1, i).join("\n").trimEnd();
              const raw = lines.slice(startLine, i + 1).join("\n");
              onFileUpdate(targetPath, newContent);
              updatedPaths.push(targetPath);
              cleanedContent = cleanedContent.replace(raw, "");
            }
          }
          i++;
        }
      }

      // Extract __file_edit blocks (4-backtick fence, search/replace for large files)
      {
        const lines = cleanedContent.split("\n");
        let i = 0;
        while (i < lines.length) {
          const openMatch = lines[i].match(/^````__file_edit:(.+)/);
          if (openMatch) {
            const targetPath = openMatch[1].trim() || fileContext.path;
            const startLine = i;
            i++;
            while (i < lines.length && !lines[i].match(/^````\s*$/)) {
              i++;
            }
            if (i < lines.length) {
              const editBlock = lines.slice(startLine + 1, i).join("\n");
              const raw = lines.slice(startLine, i + 1).join("\n");

              // Parse SEARCH/REPLACE pairs
              const pairRegex = /<<<<<<< SEARCH\n([\s\S]*?)\n=======\n([\s\S]*?)\n>>>>>>> REPLACE/g;
              let pairMatch;
              let fileContent = fileContext.getFileContent(targetPath) ?? fileContext.content;
              let applied = false;
              const failedSearches: string[] = [];

              while ((pairMatch = pairRegex.exec(editBlock)) !== null) {
                const searchText = pairMatch[1];
                const replaceText = pairMatch[2];
                if (fileContent.includes(searchText)) {
                  fileContent = fileContent.split(searchText).join(replaceText);
                  applied = true;
                } else {
                  failedSearches.push(searchText.slice(0, 50) + (searchText.length > 50 ? "..." : ""));
                }
              }

              if (applied) {
                onFileUpdate(targetPath, fileContent);
                updatedPaths.push(targetPath);
              }
              if (failedSearches.length > 0) {
                const notice = `\n\n> ${failedSearches.length} search/replace block(s) failed to match in ${targetPath}`;
                set((s) => ({
                  messages: s.messages.map((m) =>
                    m.id === assistantMsg.id ? { ...m, content: m.content + notice } : m
                  ),
                }));
              }
              cleanedContent = cleanedContent.replace(raw, "");
            }
          }
          i++;
        }
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
        try { await putSkillHistory(skillId, messages); } catch { /* best effort */ }
      }
    }
  },

  clearHistory: () => {
    const { skillId } = get();
    set({ messages: [] });
    if (skillId) {
      putSkillHistory(skillId, []);
    }
  },
}));
