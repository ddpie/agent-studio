import { create } from "zustand";
import { persist } from "zustand/middleware";
import { invokeMetaAgent, invokeAgentById } from "../lib/agentcore-client";
import i18next from "i18next";

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  images?: string[];
  attachments?: Array<{ name: string; size: number; s3Key: string }>;
  timestamp: number;
}

export interface ChatSession {
  id: string;
  agentKey: string; // agentId or "meta"
  title: string;
  messages: Message[];
  modelId?: string;
  createdAt: number;
  updatedAt: number;
}

interface ChatState {
  currentAgentId: string | null;
  currentAgentName: string | null;
  messages: Message[];
  isStreaming: boolean;
  statusText: string | null;
  activeTool: string | null;
  sessionId: string | undefined;
  activeSessionId: string | null;
  selectedModelId: string | null;
  sessions: ChatSession[];
  lastActiveSessionByAgent: Record<string, string>;

  switchAgent: (agentId: string | null, agentName: string | null) => void;
  setSelectedModel: (modelId: string) => void;
  sendMessage: (content: string, images?: string[], modelId?: string, attachments?: Array<{ name: string; size: number; s3Key: string }>) => Promise<void>;
  regenerateLastMessage: () => Promise<void>;
  editAndResend: (messageId: string, newContent: string) => Promise<void>;
  cancelStreaming: () => void;
  clearMessages: () => void;
  newSession: () => void;
  loadSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  getAgentSessions: () => ChatSession[];
}

// Module-level abort controller (not serializable, can't go in zustand persist)
let _abortController: AbortController | null = null;

function agentKey(agentId: string | null): string {
  return agentId || "meta";
}

function deriveTitle(messages: Message[]): string {
  const first = messages.find((m) => m.role === "user");
  if (!first?.content) return i18next.t("chat.newConversation", "New conversation");
  return first.content.length > 40
    ? first.content.slice(0, 40) + "..."
    : first.content;
}

// Persist-side caps. Images are expected to be S3 URLs (~150 bytes) after the
// ChatInput rewrite that refuses to send unuploaded images. These caps are a
// defence-in-depth against accidental data-URL leakage (old sessions from
// before the fix, future bugs, or Meta-Agent assistant messages).
const MAX_ACTIVE_MESSAGES = 100;
const MAX_SESSIONS = 30;
const MAX_MESSAGES_PER_SESSION = 50;

function _stripDataUrlImages(msg: Message): Message {
  if (!msg.images || msg.images.length === 0) return msg;
  const safeImages = msg.images.filter((url) => !url.startsWith("data:"));
  if (safeImages.length === msg.images.length) return msg;
  return { ...msg, images: safeImages.length > 0 ? safeImages : undefined };
}

function _sanitizeForPersist(state: ChatState): Partial<ChatState> {
  const trimmedMessages = state.messages.slice(-MAX_ACTIVE_MESSAGES).map(_stripDataUrlImages);
  const trimmedSessions = [...state.sessions]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_SESSIONS)
    .map((sess) => ({
      ...sess,
      messages: sess.messages.slice(-MAX_MESSAGES_PER_SESSION).map(_stripDataUrlImages),
    }));
  return {
    messages: trimmedMessages,
    sessionId: state.sessionId,
    activeSessionId: state.activeSessionId,
    selectedModelId: state.selectedModelId,
    currentAgentId: state.currentAgentId,
    currentAgentName: state.currentAgentName,
    sessions: trimmedSessions,
    lastActiveSessionByAgent: state.lastActiveSessionByAgent,
  };
}

// Default values for every persisted/volatile field in ChatState. Used both
// when constructing the store and when resetting it on workspace switch.
const _chatInitialState = {
  currentAgentId: null,
  currentAgentName: null,
  messages: [] as Message[],
  isStreaming: false,
  statusText: null,
  activeTool: null,
  sessionId: undefined,
  activeSessionId: null,
  selectedModelId: null,
  sessions: [] as ChatSession[],
  lastActiveSessionByAgent: {} as Record<string, string>,
};

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      ..._chatInitialState,

      getAgentSessions: () => {
        const { currentAgentId, sessions } = get();
        const key = agentKey(currentAgentId);
        return sessions
          .filter((s) => s.agentKey === key)
          .sort((a, b) => b.updatedAt - a.updatedAt);
      },

      switchAgent: (agentId, agentName) => {
        // Save current session first (may create a new session & update activeSessionId)
        _saveCurrentSession(get(), set);

        // Read fresh state AFTER save — state.activeSessionId may have changed
        const fresh = get();
        const currentKey = agentKey(fresh.currentAgentId);
        if (fresh.activeSessionId) {
          set((s) => ({
            lastActiveSessionByAgent: { ...s.lastActiveSessionByAgent, [currentKey]: fresh.activeSessionId! },
          }));
        }

        // Restore last active session for the target agent, or fall back to most recent
        const key = agentKey(agentId);
        const updated = get();
        const lastActiveId = updated.lastActiveSessionByAgent[key];
        const lastActive = lastActiveId
          ? updated.sessions.find((s) => s.id === lastActiveId)
          : null;
        const recent = lastActive || updated.sessions
          .filter((s) => s.agentKey === key)
          .sort((a, b) => b.updatedAt - a.updatedAt)[0];

        if (recent) {
          set({
            currentAgentId: agentId,
            currentAgentName: agentName,
            messages: recent.messages,
            activeSessionId: recent.id,
            selectedModelId: recent.modelId || null,
            sessionId: undefined,
            statusText: null,
            activeTool: null,
          });
        } else {
          set({
            currentAgentId: agentId,
            currentAgentName: agentName,
            messages: [],
            sessionId: undefined,
            activeSessionId: null,
            selectedModelId: null,
            statusText: null,
            activeTool: null,
          });
        }
      },

      newSession: () => {
        const state = get();
        _saveCurrentSession(state, set);
        set({
          messages: [],
          sessionId: undefined,
          activeSessionId: null,
          statusText: null,
          activeTool: null,
        });
      },

      loadSession: (sessionId: string) => {
        const state = get();
        _saveCurrentSession(state, set);
        const session = state.sessions.find((s) => s.id === sessionId);
        if (session) {
          set({
            messages: session.messages,
            activeSessionId: session.id,
            selectedModelId: session.modelId || null,
            // Drop the in-flight AgentCore sessionId — next sendMessage
            // will mint a fresh one, so the loaded history starts a new
            // server-side session (may be a different AgentCore container).
            sessionId: undefined,
            statusText: null,
          });
        }
      },

      deleteSession: (sessionId: string) => {
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.id !== sessionId),
          // If deleting the active session, clear messages
          ...(s.activeSessionId === sessionId
            ? { messages: [], activeSessionId: null, sessionId: undefined }
            : {}),
        }));
      },

      setSelectedModel: (modelId: string) => set({ selectedModelId: modelId }),

      sendMessage: async (content: string, images?: string[], modelId?: string, attachments?: Array<{ name: string; size: number; s3Key: string }>) => {
        _abortController = new AbortController();
        const signal = _abortController.signal;

        const userMsg: Message = {
          id: crypto.randomUUID(),
          role: "user",
          content,
          images: images && images.length > 0 ? images : undefined,
          attachments: attachments && attachments.length > 0 ? attachments : undefined,
          timestamp: Date.now(),
        };

        const assistantMsg: Message = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "",
          timestamp: Date.now(),
        };

        // Reuse one AgentCore session id across turns within the same chat.
        // Each turn still gets its own traceId, so the Runs tab groups them
        // as one session with N turns (matches LangSmith/Langfuse UX). The
        // `newSession` / `loadSession` / `clearMessages` actions reset this
        // back to undefined so a fresh chat gets a fresh session id.
        let turnSessionId = get().sessionId;
        if (!turnSessionId) {
          turnSessionId = crypto.randomUUID();
          set({ sessionId: turnSessionId });
        }

        set((s) => ({
          messages: [...s.messages, userMsg, assistantMsg],
          isStreaming: true,
          statusText: null,
        }));

        const onStatus = (status: string | null) => {
          set({ statusText: status });
        };

        try {
          const { currentAgentId } = get();
          let stream: AsyncGenerator<string>;

          if (currentAgentId) {
            const history = get().messages
              .filter((m) => m.id !== assistantMsg.id && m.content && m.role !== "system")
              .map(({ role, content }) => ({ role: role as "user" | "assistant", content }));
            stream = invokeAgentById(currentAgentId, content, history, turnSessionId, onStatus, images, modelId);
          } else {
            const history = get().messages
              .filter((m) => m.id !== assistantMsg.id && m.content && m.role !== "system")
              .map(({ role, content }) => ({ role: role as "user" | "assistant", content }));
            stream = invokeMetaAgent(content, history, turnSessionId, onStatus, images, modelId);
          }

          // Batch chunks to reduce re-renders: accumulate text, flush every 80ms
          let pendingText = "";
          let flushTimer: ReturnType<typeof setTimeout> | null = null;

          const flushPending = () => {
            if (!pendingText) return;
            const text = pendingText;
            pendingText = "";
            set((s) => ({
              messages: s.messages.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, content: m.content + text }
                  : m
              ),
            }));
          };

          const scheduleFlush = () => {
            if (!flushTimer) {
              flushTimer = setTimeout(() => {
                flushTimer = null;
                flushPending();
              }, 50);
            }
          };

          let toolBuffer = "";  // Buffer for incomplete tool markers across chunks

          for await (const chunk of stream) {
            if (signal.aborted) break;

            // Tool markers are JSON with __tool key. Buffer across chunks for split markers.
            const toolJsonRe = /\{"__tool"[^}]*\}/g;
            toolBuffer += chunk;
            let remaining = "";
            const markers: { type: string; name: string; input?: string; output?: string }[] = [];

            let jsonMatch: RegExpExecArray | null;
            let lastMatchEnd = 0;
            while ((jsonMatch = toolJsonRe.exec(toolBuffer)) !== null) {
              try {
                const parsed = JSON.parse(jsonMatch[0]);
                if (parsed.__tool && parsed.name) {
                  markers.push({ type: parsed.__tool, name: parsed.name, input: parsed.input, output: parsed.output });
                }
              } catch { /* skip */ }
              lastMatchEnd = jsonMatch.index + jsonMatch[0].length;
            }

            // Check if there's an incomplete marker at the end (starts with {"__tool but no closing })
            const lastOpenBrace = toolBuffer.lastIndexOf('{"__tool"');
            if (lastOpenBrace >= lastMatchEnd) {
              // Incomplete marker — keep in buffer, emit text before it
              remaining = toolBuffer.slice(0, lastOpenBrace).replace(toolJsonRe, "");
              toolBuffer = toolBuffer.slice(lastOpenBrace);
            } else {
              // All markers matched — emit remaining text, clear buffer
              remaining = toolBuffer.replace(toolJsonRe, "");
              toolBuffer = "";
            }

            if (markers.length > 0) {
              // Flush any pending text before inserting tool markers
              flushPending();

              for (const m of markers) {
                if (m.type === "start") {
                  set({ activeTool: m.name });
                } else if (m.type === "result") {
                  let inp = "", out = "";
                  try { inp = m.input ? new TextDecoder().decode(Uint8Array.from(atob(m.input), c => c.charCodeAt(0))) : ""; } catch { inp = m.input || ""; }
                  try { out = m.output ? new TextDecoder().decode(Uint8Array.from(atob(m.output), c => c.charCodeAt(0))) : ""; } catch { out = m.output || ""; }
                  let detailContent = `\n\n<details class="tool-call"><summary>Called <strong>${m.name}</strong></summary>\n\n`;
                  if (inp) detailContent += `**Input:**\n\`\`\`json\n${inp}\n\`\`\`\n`;
                  // Only render inline SVG (charts/diagrams). All other outputs use code blocks.
                  const isSvg = out && out.trimStart().startsWith("<svg");
                  if (out && !isSvg) {
                    const maxDisplay = 2000;
                    const truncated = out.length > maxDisplay ? out.slice(0, maxDisplay) + "\n... (truncated)" : out;
                    const escaped = truncated.replace(/```/g, "\\`\\`\\`");
                    detailContent += `**Output:**\n\`\`\`\n${escaped}\n\`\`\`\n`;
                  } else if (isSvg) {
                    detailContent += `**Output:** Chart rendered below.\n`;
                  }
                  detailContent += `\n</details>\n\n`;
                  if (isSvg) {
                    detailContent += `\n\n<div class="tool-rich-output">${out}</div>\n\n`;
                  }
                  set((s) => ({
                    messages: s.messages.map((msg) =>
                      msg.id === assistantMsg.id
                        ? { ...msg, content: msg.content + detailContent }
                        : msg
                    ),
                  }));
                } else if (m.type === "end") {
                  set({ activeTool: null });
                }
              }
            }

            if (!remaining) continue;

            pendingText += remaining;
            scheduleFlush();
          }

          // Final flush
          if (flushTimer) clearTimeout(flushTimer);
          flushPending();
        } catch (err) {
          if (!signal.aborted) {
            set((s) => ({
              messages: s.messages.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, content: i18next.t("chat.errorPrefix", "Error") + `: ${err instanceof Error ? err.message : "Unknown error"}` }
                  : m
              ),
            }));
          }
        } finally {
          _abortController = null;
          set({ isStreaming: false, statusText: null, activeTool: null });
        }
      },

      clearMessages: () => {
        const state = get();
        _saveCurrentSession(state, set);
        set({ messages: [], sessionId: undefined, activeSessionId: null, statusText: null });
      },

      cancelStreaming: () => {
        if (_abortController) {
          _abortController.abort();
          _abortController = null;
        }
        set({ isStreaming: false, statusText: null });
      },

      regenerateLastMessage: async () => {
        const { messages, isStreaming } = get();
        if (isStreaming) return;

        // Find the last user message
        const lastUserIdx = messages.findLastIndex((m) => m.role === "user");
        if (lastUserIdx === -1) return;

        const lastUserMsg = messages[lastUserIdx];
        const content = lastUserMsg.content;
        const images = lastUserMsg.images;
        const attachments = lastUserMsg.attachments;

        // Remove the last assistant message (and the user message to re-send)
        const trimmed = messages.slice(0, lastUserIdx);
        set({ messages: trimmed });

        // Re-send using the current model
        const modelId = get().selectedModelId || undefined;
        await get().sendMessage(content, images, modelId, attachments);
      },

      editAndResend: async (messageId: string, newContent: string) => {
        const { messages, isStreaming } = get();
        if (isStreaming) return;

        const msgIdx = messages.findIndex((m) => m.id === messageId);
        if (msgIdx === -1) return;

        // Truncate everything from this message onward
        const trimmed = messages.slice(0, msgIdx);
        const images = messages[msgIdx].images;
        const attachments = messages[msgIdx].attachments;
        set({ messages: trimmed });

        const modelId = get().selectedModelId || undefined;
        await get().sendMessage(newContent, images, modelId, attachments);
      },
    }),
    {
      name: "agent-studio-chat",
      partialize: (state) => _sanitizeForPersist(state),
    }
  )
);

/**
 * Reset chat state on workspace switch.
 *
 * Persisted sessions/currentAgentId/lastActiveSessionByAgent reference agent
 * IDs scoped to a single workspace — after switching workspaces those IDs are
 * stale and would render as broken links. Clear both the in-memory store and
 * the persisted localStorage copy.
 *
 * Intentionally does NOT touch `agent-studio-ui` (workspace-agnostic) or
 * `agent-studio-workspace-id` (identifies the workspace itself).
 */
export function resetChatForWorkspaceSwitch(): void {
  // Abort any in-flight stream before wiping state.
  if (_abortController) {
    try { _abortController.abort(); } catch { /* ignore */ }
    _abortController = null;
  }
  // Partial merge — keep action methods (switchAgent, sendMessage, etc.)
  // intact while resetting data fields.
  useChatStore.setState(_chatInitialState);
  try {
    useChatStore.persist.clearStorage();
  } catch {
    // clearStorage can throw if storage is unavailable — safe to ignore.
  }
}

/** Save current messages as a session (if non-empty). */
function _saveCurrentSession(
  state: ChatState,
  set: (partial: Partial<ChatState> | ((s: ChatState) => Partial<ChatState>)) => void
) {
  const msgs = state.messages.filter((m) => m.content);
  if (msgs.length === 0) return;

  const key = agentKey(state.currentAgentId);
  const now = Date.now();

  if (state.activeSessionId) {
    // Update existing session
    set((s) => ({
      sessions: s.sessions.map((sess) =>
        sess.id === state.activeSessionId
          ? { ...sess, messages: msgs, title: deriveTitle(msgs), modelId: state.selectedModelId || undefined, updatedAt: now }
          : sess
      ),
    }));
  } else {
    // Create new session
    const newSession: ChatSession = {
      id: crypto.randomUUID(),
      agentKey: key,
      title: deriveTitle(msgs),
      messages: msgs,
      modelId: state.selectedModelId || undefined,
      createdAt: now,
      updatedAt: now,
    };
    set((s) => ({
      sessions: [...s.sessions, newSession],
      activeSessionId: newSession.id,
    }));
  }
}
