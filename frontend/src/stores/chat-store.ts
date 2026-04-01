import { create } from "zustand";
import { persist } from "zustand/middleware";
import { invokeMetaAgent, invokeAgentById } from "../lib/agentcore-client";

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  images?: string[];
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
  targetAgentId: string | null;
  targetAgentName: string | null;
  messages: Message[];
  isStreaming: boolean;
  statusText: string | null;
  activeTool: string | null;
  sessionId: string | undefined;
  activeSessionId: string | null;
  selectedModelId: string | null;
  sessions: ChatSession[];

  setTarget: (agentId: string | null, agentName: string | null) => void;
  setSelectedModel: (modelId: string) => void;
  sendMessage: (content: string, images?: string[], modelId?: string) => Promise<void>;
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
  if (!first?.content) return "New conversation";
  return first.content.length > 40
    ? first.content.slice(0, 40) + "..."
    : first.content;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      targetAgentId: null,
      targetAgentName: null,
      messages: [],
      isStreaming: false,
      statusText: null,
      activeTool: null,
      sessionId: undefined,
      activeSessionId: null,
      selectedModelId: null,
      sessions: [],

      getAgentSessions: () => {
        const { targetAgentId, sessions } = get();
        const key = agentKey(targetAgentId);
        return sessions
          .filter((s) => s.agentKey === key)
          .sort((a, b) => b.updatedAt - a.updatedAt);
      },

      setTarget: (agentId, agentName) => {
        const state = get();
        // Save current session before switching
        _saveCurrentSession(state, set);

        // Find the most recent session for the target agent
        const key = agentKey(agentId);
        const recent = state.sessions
          .filter((s) => s.agentKey === key)
          .sort((a, b) => b.updatedAt - a.updatedAt)[0];

        if (recent) {
          set({
            targetAgentId: agentId,
            targetAgentName: agentName,
            messages: recent.messages,
            activeSessionId: recent.id,
            sessionId: undefined,
            statusText: null,
          });
        } else {
          set({
            targetAgentId: agentId,
            targetAgentName: agentName,
            messages: [],
            sessionId: undefined,
            activeSessionId: null,
            statusText: null,
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

      sendMessage: async (content: string, images?: string[], modelId?: string) => {
        _abortController = new AbortController();
        const signal = _abortController.signal;

        const userMsg: Message = {
          id: crypto.randomUUID(),
          role: "user",
          content,
          images: images && images.length > 0 ? images : undefined,
          timestamp: Date.now(),
        };

        const assistantMsg: Message = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "",
          timestamp: Date.now(),
        };

        set((s) => ({
          messages: [...s.messages, userMsg, assistantMsg],
          isStreaming: true,
          statusText: null,
        }));

        const onStatus = (status: string | null) => {
          set({ statusText: status });
        };

        try {
          const { targetAgentId } = get();
          let stream: AsyncGenerator<string>;

          if (targetAgentId) {
            const history = get().messages
              .filter((m) => m.id !== assistantMsg.id && m.content && m.role !== "system")
              .map(({ role, content }) => ({ role: role as "user" | "assistant", content }));
            stream = invokeAgentById(targetAgentId, content, history, get().sessionId, onStatus, images, modelId);
          } else {
            const history = get().messages
              .filter((m) => m.id !== assistantMsg.id && m.content && m.role !== "system")
              .map(({ role, content }) => ({ role: role as "user" | "assistant", content }));
            stream = invokeMetaAgent(content, history, get().sessionId, onStatus, images, modelId);
          }

          for await (const chunk of stream) {
            if (signal.aborted) break;

            // Detect tool-use markers from agent stream
            if (chunk.startsWith('{"__tool"')) {
              try {
                const toolEvent = JSON.parse(chunk);
                if (toolEvent.__tool === "start") {
                  set({ activeTool: toolEvent.name });
                } else if (toolEvent.__tool === "end") {
                  set({ activeTool: null });
                }
                continue; // Don't append JSON marker to message content
              } catch { /* not valid JSON, treat as text */ }
            }

            set((s) => ({
              messages: s.messages.map((m) =>
                m.id === assistantMsg.id
                  ? { ...m, content: m.content + chunk }
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

        // Remove the last assistant message (and the user message to re-send)
        const trimmed = messages.slice(0, lastUserIdx);
        set({ messages: trimmed });

        // Re-send using the current model
        const modelId = get().selectedModelId || undefined;
        await get().sendMessage(content, images, modelId);
      },

      editAndResend: async (messageId: string, newContent: string) => {
        const { messages, isStreaming } = get();
        if (isStreaming) return;

        const msgIdx = messages.findIndex((m) => m.id === messageId);
        if (msgIdx === -1) return;

        // Truncate everything from this message onward
        const trimmed = messages.slice(0, msgIdx);
        const images = messages[msgIdx].images;
        set({ messages: trimmed });

        const modelId = get().selectedModelId || undefined;
        await get().sendMessage(newContent, images, modelId);
      },
    }),
    {
      name: "agent-studio-chat",
      partialize: (state) => ({
        messages: state.messages,
        sessionId: state.sessionId,
        activeSessionId: state.activeSessionId,
        selectedModelId: state.selectedModelId,
        targetAgentId: state.targetAgentId,
        targetAgentName: state.targetAgentName,
        sessions: state.sessions,
      }),
    }
  )
);

/** Save current messages as a session (if non-empty). */
function _saveCurrentSession(
  state: ChatState,
  set: (partial: Partial<ChatState> | ((s: ChatState) => Partial<ChatState>)) => void
) {
  const msgs = state.messages.filter((m) => m.content);
  if (msgs.length === 0) return;

  const key = agentKey(state.targetAgentId);
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
