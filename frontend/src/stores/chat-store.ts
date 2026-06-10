import { create } from "zustand";
import { persist } from "zustand/middleware";
import { useShallow } from "zustand/react/shallow";
import { invokeMetaAgent, invokeAgentById } from "../lib/agentcore-client";
import {
  listChatSessions,
  getChatSession,
  putChatSession,
  deleteChatSession,
} from "../lib/api-client";
import i18next from "i18next";

/**
 * A structured record of one tool invocation inside an assistant turn.
 *
 * Kept as a sidecar field on Message (not inlined into `content`) so the
 * model never sees a textual `<details class="tool-call">` block or the
 * `__S3_DOWNLOAD__` marker in subsequent turns. Before this refactor the
 * frontend injected that markdown into `message.content`, which then got
 * replayed to the agent as the assistant's own prior output and the
 * model copied the format, hallucinating fake S3 keys.
 */
export interface ToolCallRecord {
  id: string;
  name: string;
  input: string;
  output: string;
  /** True if output is an inline SVG chart/diagram — rendered specially. */
  isSvg: boolean;
}

/** S3 object the agent uploaded for the user to download. */
export interface S3Download {
  key: string;
  filename: string;
}

/**
 * Ordered render unit used to interleave assistant prose with tool call
 * UI in the transcript. Each text block may be appended to as streaming
 * continues; tool_call blocks carry a snapshot of the ToolCallRecord.
 *
 * The concatenation of all `text` blocks equals `Message.content` — this
 * invariant lets everything outside the renderer (history replay, export,
 * edit-and-resend, migration) keep working without caring about blocks.
 */
export type MessageBlock =
  | { kind: "text"; text: string }
  | { kind: "tool_call"; call: ToolCallRecord }
  | { kind: "error"; text: string };

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  /**
   * Prose-only text. Must NOT contain tool-call `<details>` blocks or
   * `__S3_DOWNLOAD__` markers — those live on `toolCalls` / `s3Downloads`.
   */
  content: string;
  images?: string[];
  attachments?: { name: string; size: number; s3Key: string }[];
  /** Tool invocations captured during the turn (streaming order). */
  toolCalls?: ToolCallRecord[];
  /**
   * Inline render sequence. When present, ChatMessage renders prose and
   * tool calls in arrival order so readers see the model's "then I
   * called X, then said Y" flow. Absent on legacy messages (pre-blocks
   * migration) and on messages rendered with the `showInlineToolCalls`
   * setting off — both fall back to `content + toolCalls` stacked.
   */
  blocks?: MessageBlock[];
  /** Files the agent uploaded for download (deduped by key). */
  s3Downloads?: S3Download[];
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

/**
 * State is keyed per-agent so navigating away from a chat mid-stream no longer
 * drops chunks. Each agent carries its own message list, streaming flag,
 * active-tool indicator, AgentCore session id, and selected model. The UI
 * selects the slice for `currentAgentId` via `useCurrentAgentChat()`.
 */
interface ChatState {
  currentAgentId: string | null;
  currentAgentName: string | null;

  messagesByAgent: Record<string, Message[]>;
  streamingByAgent: Record<string, boolean>;
  statusByAgent: Record<string, string | null>;
  activeToolByAgent: Record<string, string | null>;
  // { n: current round 1..max, max }, or null when not auto-continuing.
  // Meta-Agent's supervisor emits this when it re-prompts Kiro after a
  // premature end_turn; the ChatPanel renders a small "(auto-continuing
  // N/M)" badge. Cleared on the first real text chunk of the next round.
  autoContinueByAgent: Record<string, { n: number; max: number } | null>;
  sessionIdByAgent: Record<string, string | undefined>;
  activeSessionByAgent: Record<string, string | null>;
  selectedModelByAgent: Record<string, string | null>;

  sessions: ChatSession[];
  lastActiveSessionByAgent: Record<string, string>;

  switchAgent: (agentId: string | null, agentName: string | null) => void;
  setSelectedModel: (modelId: string) => void;
  sendMessage: (content: string, images?: string[], modelId?: string, attachments?: { name: string; size: number; s3Key: string }[]) => Promise<void>;
  regenerateLastMessage: () => Promise<void>;
  editAndResend: (messageId: string, newContent: string) => Promise<void>;
  cancelStreaming: () => void;
  clearMessages: () => void;
  newSession: () => void;
  loadSession: (sessionId: string) => void;
  deleteSession: (sessionId: string) => void;
  getAgentSessions: () => ChatSession[];
}

/**
 * AbortControllers are stored per-agent so the user can have two agents
 * streaming concurrently without one cancelling the other. Held at module
 * scope because AbortController is not serializable by zustand persist.
 */
const _abortControllersByAgent = new Map<string, AbortController>();

export function agentKey(agentId: string | null): string {
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
// defence-in-depth against accidental data-URL leakage.
const MAX_ACTIVE_MESSAGES = 100;
const MAX_SESSIONS = 30;
const MAX_MESSAGES_PER_SESSION = 50;

function _stripDataUrlImages(msg: Message): Message {
  if (!msg.images || msg.images.length === 0) return msg;
  const safeImages = msg.images.filter((url) => !url.startsWith("data:"));
  if (safeImages.length === msg.images.length) return msg;
  return { ...msg, images: safeImages.length > 0 ? safeImages : undefined };
}

/**
 * v1/v2 → v3 migration of per-message content: prior versions stored
 * tool-call `<details>` blocks, `tool-rich-output` divs, and
 * `__S3_DOWNLOAD__` markers inline in `message.content`. The current
 * version stores the same data on `toolCalls` / `s3Downloads` sidecars so
 * the model can't echo the text format back as a hallucinated tool call.
 * We can't faithfully reconstruct the original structured toolCalls from
 * rendered markdown, so legacy messages just lose the tool-call affordance
 * — the prose survives and `s3Downloads` is recovered from the marker list
 * so old download buttons keep working (subject to the S3 object still
 * existing; `S3DownloadList` falls back to "file no longer available").
 */
/** @internal exported for tests */
export function _migrateLegacyMessage(msg: Message): Message {
  if (typeof msg.content !== "string") return msg;
  let text = msg.content;
  const hasLegacy =
    text.includes('<details class="tool-call">') ||
    text.includes('<div class="tool-rich-output">') ||
    text.includes("__S3_DOWNLOAD__:");
  if (!hasLegacy) return msg;

  const downloads: S3Download[] = [...(msg.s3Downloads || [])];
  const seen = new Set(downloads.map((d) => d.key));
  for (const m of text.matchAll(/__S3_DOWNLOAD__:([^:\s"}\]]+):([^\s"}\]]+)/g)) {
    const key = m[1], filename = m[2];
    if (!seen.has(key)) { seen.add(key); downloads.push({ key, filename }); }
  }

  text = text
    .replace(/<details class="tool-call">[\s\S]*?<\/details>/g, "")
    .replace(/<div class="tool-rich-output">[\s\S]*?<\/div>/g, "")
    .replace(/__S3_DOWNLOAD__:[^\s"}\]]+/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return {
    ...msg,
    content: text,
    s3Downloads: downloads.length > 0 ? downloads : msg.s3Downloads,
  };
}

/**
 * Persisted shape in v1/v2 used top-level `messages`, `sessionId`,
 * `activeSessionId`, `selectedModelId`. v3 stores these per-agent.
 * Move any v1/v2 top-level slice into the bucket for the agent that was
 * current at persist time, preserving user's last in-progress context.
 */
interface LegacyPersistedState {
  currentAgentId?: string | null;
  messages?: Message[];
  sessionId?: string;
  activeSessionId?: string | null;
  selectedModelId?: string | null;
  sessions?: ChatSession[];
  lastActiveSessionByAgent?: Record<string, string>;
  messagesByAgent?: Record<string, Message[]>;
  sessionIdByAgent?: Record<string, string | undefined>;
  activeSessionByAgent?: Record<string, string | null>;
  selectedModelByAgent?: Record<string, string | null>;
}

/** @internal exported for tests */
export function _migrateToV3(persisted: LegacyPersistedState): Partial<ChatState> {
  const key = agentKey(persisted.currentAgentId ?? null);

  // v2-era migration: clean legacy inline markers out of every message.
  const cleanMessages = (msgs: Message[] | undefined) =>
    (msgs || []).map(_migrateLegacyMessage);

  const sessions = (persisted.sessions || []).map((sess) => ({
    ...sess,
    messages: cleanMessages(sess.messages),
  }));

  const messagesByAgent: Record<string, Message[]> = {};
  for (const [k, v] of Object.entries(persisted.messagesByAgent || {})) {
    messagesByAgent[k] = cleanMessages(v);
  }
  // If persisted state still has the v1/v2 flat `messages`, park it under
  // the agent that was current at persist time (falling back to "meta").
  if (Array.isArray(persisted.messages) && persisted.messages.length > 0 && !messagesByAgent[key]) {
    messagesByAgent[key] = cleanMessages(persisted.messages);
  }

  const sessionIdByAgent: Record<string, string | undefined> = { ...(persisted.sessionIdByAgent || {}) };
  if (persisted.sessionId && !sessionIdByAgent[key]) sessionIdByAgent[key] = persisted.sessionId;

  const activeSessionByAgent: Record<string, string | null> = { ...(persisted.activeSessionByAgent || {}) };
  if (persisted.activeSessionId !== undefined && !(key in activeSessionByAgent)) {
    activeSessionByAgent[key] = persisted.activeSessionId ?? null;
  }

  const selectedModelByAgent: Record<string, string | null> = { ...(persisted.selectedModelByAgent || {}) };
  if (persisted.selectedModelId !== undefined && !(key in selectedModelByAgent)) {
    selectedModelByAgent[key] = persisted.selectedModelId ?? null;
  }
  // Format guard: the "meta" slot holds Meta-Agent's Kiro-native id
  // (e.g. "claude-opus-4.6"). Before B-4 the store was Bedrock-only —
  // persisted `us.anthropic.*` / `global.anthropic.*` / region-prefixed
  // values in the "meta" slot would get sent to Kiro as model_id and
  // silently fail. Scrub any such legacy value so the store falls back
  // to the Kiro default on next read.
  const BEDROCK_ID_RE = /^(us|global|apac|eu)\./;
  const metaId = selectedModelByAgent.meta;
  if (typeof metaId === "string" && BEDROCK_ID_RE.test(metaId)) {
    selectedModelByAgent.meta = null;
  }

  return {
    currentAgentId: persisted.currentAgentId ?? null,
    messagesByAgent,
    sessionIdByAgent,
    activeSessionByAgent,
    selectedModelByAgent,
    sessions,
    lastActiveSessionByAgent: persisted.lastActiveSessionByAgent || {},
  };
}

function _sanitizeForPersist(state: ChatState): Partial<ChatState> {
  // Cap per-agent messages so one runaway conversation can't bloat
  // localStorage. Same cap applied independently per agent; streaming
  // state (streamingByAgent etc.) is deliberately not persisted.
  const trimmedMessagesByAgent: Record<string, Message[]> = {};
  for (const [k, msgs] of Object.entries(state.messagesByAgent)) {
    const trimmed = msgs.slice(-MAX_ACTIVE_MESSAGES).map(_stripDataUrlImages);
    if (trimmed.length > 0) trimmedMessagesByAgent[k] = trimmed;
  }
  const trimmedSessions = [...state.sessions]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_SESSIONS)
    .map((sess) => ({
      ...sess,
      messages: sess.messages.slice(-MAX_MESSAGES_PER_SESSION).map(_stripDataUrlImages),
    }));
  return {
    messagesByAgent: trimmedMessagesByAgent,
    sessionIdByAgent: state.sessionIdByAgent,
    activeSessionByAgent: state.activeSessionByAgent,
    selectedModelByAgent: state.selectedModelByAgent,
    currentAgentId: state.currentAgentId,
    currentAgentName: state.currentAgentName,
    sessions: trimmedSessions,
    lastActiveSessionByAgent: state.lastActiveSessionByAgent,
  };
}

// Default values for every persisted/volatile field. Used both
// when constructing the store and when resetting it on workspace switch.
const _chatInitialState = {
  currentAgentId: null,
  currentAgentName: null,
  messagesByAgent: {} as Record<string, Message[]>,
  streamingByAgent: {} as Record<string, boolean>,
  statusByAgent: {} as Record<string, string | null>,
  activeToolByAgent: {} as Record<string, string | null>,
  autoContinueByAgent: {} as Record<string, { n: number; max: number } | null>,
  sessionIdByAgent: {} as Record<string, string | undefined>,
  activeSessionByAgent: {} as Record<string, string | null>,
  selectedModelByAgent: {} as Record<string, string | null>,
  sessions: [] as ChatSession[],
  lastActiveSessionByAgent: {} as Record<string, string>,
};

// ── Small helpers that mutate a per-agent bucket in an immutable way ────

function setMessagesFor(
  state: ChatState,
  key: string,
  updater: (msgs: Message[]) => Message[],
): Partial<ChatState> {
  const prev = state.messagesByAgent[key] || [];
  return { messagesByAgent: { ...state.messagesByAgent, [key]: updater(prev) } };
}

function setFlagFor<K extends
  | "streamingByAgent"
  | "statusByAgent"
  | "activeToolByAgent"
  | "autoContinueByAgent"
  | "sessionIdByAgent"
  | "activeSessionByAgent"
  | "selectedModelByAgent">(
  state: ChatState,
  field: K,
  key: string,
  value: ChatState[K][string],
): Partial<ChatState> {
  return { [field]: { ...state[field], [key]: value } } as Partial<ChatState>;
}

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
        // Save whatever is live for the current agent before switching.
        // Any in-flight stream for the previous agent keeps running and
        // keeps writing into its own bucket — not cancelled.
        const state = get();
        _saveCurrentSessionAndSync(state, set);
        // Kick off a one-time cloud hydrate for the target agent. No-op if
        // we've already hydrated this agentKey in this browser session.
        void hydrateSessionsFromCloud(agentId);

        const fresh = get();
        const currentKey = agentKey(fresh.currentAgentId);
        const activeId = fresh.activeSessionByAgent[currentKey] ?? null;
        if (activeId) {
          set((s) => ({
            lastActiveSessionByAgent: { ...s.lastActiveSessionByAgent, [currentKey]: activeId },
          }));
        }

        // Resolve target agent's last session (for restoring UI state on
        // first visit in this browser session). We do NOT overwrite
        // messagesByAgent[targetKey] — the agent's live bucket is the
        // source of truth; we only restore from sessions if the bucket
        // is empty (e.g. on fresh page load).
        const targetKey = agentKey(agentId);
        const updated = get();
        const bucketHasContent = (updated.messagesByAgent[targetKey] || []).length > 0;
        if (!bucketHasContent) {
          const lastActiveId = updated.lastActiveSessionByAgent[targetKey];
          const lastActive = lastActiveId
            ? updated.sessions.find((s) => s.id === lastActiveId)
            : null;
          const recent = lastActive || updated.sessions
            .filter((s) => s.agentKey === targetKey)
            .sort((a, b) => b.updatedAt - a.updatedAt)[0];
          if (recent) {
            set((s) => ({
              messagesByAgent: { ...s.messagesByAgent, [targetKey]: recent.messages },
              activeSessionByAgent: { ...s.activeSessionByAgent, [targetKey]: recent.id },
              selectedModelByAgent: { ...s.selectedModelByAgent, [targetKey]: recent.modelId || null },
              sessionIdByAgent: { ...s.sessionIdByAgent, [targetKey]: undefined },
            }));
          }
        }

        set({ currentAgentId: agentId, currentAgentName: agentName });
      },

      newSession: () => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        _saveCurrentSessionAndSync(state, set);
        set((s) => ({
          ...setMessagesFor(s, key, () => []),
          ...setFlagFor(s, "sessionIdByAgent", key, undefined),
          ...setFlagFor(s, "activeSessionByAgent", key, null),
          ...setFlagFor(s, "statusByAgent", key, null),
          ...setFlagFor(s, "activeToolByAgent", key, null),
        }));
      },

      loadSession: (sessionId: string) => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        _saveCurrentSessionAndSync(state, set);
        const session = state.sessions.find((s) => s.id === sessionId);
        if (session) {
          set((s) => ({
            ...setMessagesFor(s, key, () => session.messages),
            ...setFlagFor(s, "activeSessionByAgent", key, session.id),
            ...setFlagFor(s, "selectedModelByAgent", key, session.modelId || null),
            // Drop the in-flight AgentCore sessionId — next sendMessage
            // will mint a fresh one so loaded history starts a new
            // server-side session (may be a different container).
            ...setFlagFor(s, "sessionIdByAgent", key, undefined),
            ...setFlagFor(s, "statusByAgent", key, null),
          }));
        }
      },

      deleteSession: (sessionId: string) => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        const isActive = state.activeSessionByAgent[key] === sessionId;
        // Cancel any queued cloud save for this session before deleting —
        // a late PUT would resurrect it.
        const pending = _cloudSaveTimers.get(sessionId);
        if (pending) {
          clearTimeout(pending);
          _cloudSaveTimers.delete(sessionId);
        }
        const target = state.sessions.find((s) => s.id === sessionId);
        set((s) => ({
          sessions: s.sessions.filter((sess) => sess.id !== sessionId),
          ...(isActive
            ? {
                ...setMessagesFor(s, key, () => []),
                ...setFlagFor(s, "activeSessionByAgent", key, null),
                ...setFlagFor(s, "sessionIdByAgent", key, undefined),
              }
            : {}),
        }));
        if (target) void deleteChatSession(target.agentKey, target.id);
      },

      setSelectedModel: (modelId: string) => {
        const key = agentKey(get().currentAgentId);
        set((s) => setFlagFor(s, "selectedModelByAgent", key, modelId));
      },

      sendMessage: async (content: string, images?: string[], modelId?: string, attachments?: { name: string; size: number; s3Key: string }[]) => {
        // Snapshot the agent at send time. All subsequent writes target
        // this key — user may navigate away and the live
        // `currentAgentId` may drift, but chunks must still land in the
        // bucket of the agent that was queried.
        const sendingAgentKey = agentKey(get().currentAgentId);
        const sendingAgentId = get().currentAgentId;

        // Per-agent abort controller so concurrent streams don't cancel
        // each other. Abort any prior stream for this same agent first
        // (defensive — UI disables the send button while streaming).
        _abortControllersByAgent.get(sendingAgentKey)?.abort();
        const controller = new AbortController();
        _abortControllersByAgent.set(sendingAgentKey, controller);
        const signal = controller.signal;

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

        // Reuse one AgentCore session id across turns within the same
        // chat for the same agent. Each turn still gets its own traceId,
        // so the Runs tab groups them as one session with N turns.
        // newSession / loadSession / clearMessages clear this so a fresh
        // chat gets a fresh session id.
        let turnSessionId = get().sessionIdByAgent[sendingAgentKey];
        if (!turnSessionId) {
          turnSessionId = crypto.randomUUID();
          set((s) => setFlagFor(s, "sessionIdByAgent", sendingAgentKey, turnSessionId));
        }

        set((s) => ({
          ...setMessagesFor(s, sendingAgentKey, (msgs) => [...msgs, userMsg, assistantMsg]),
          ...setFlagFor(s, "streamingByAgent", sendingAgentKey, true),
          ...setFlagFor(s, "statusByAgent", sendingAgentKey, null),
        }));

        const onStatus = (status: string | null) => {
          set((s) => setFlagFor(s, "statusByAgent", sendingAgentKey, status));
        };

        try {
          let stream: AsyncGenerator<string>;

          // History snapshot from the sending agent's bucket, excluding
          // the placeholder assistant message we just pushed.
          const historyMsgs = (get().messagesByAgent[sendingAgentKey] || [])
            .filter((m) => m.id !== assistantMsg.id && m.content && m.role !== "system")
            .map(({ role, content }) => ({ role: role as "user" | "assistant", content }));

          if (sendingAgentId) {
            stream = invokeAgentById(sendingAgentId, content, historyMsgs, turnSessionId, onStatus, images, modelId);
          } else {
            stream = invokeMetaAgent(content, historyMsgs, turnSessionId, onStatus, images, modelId);
          }

          // Batch chunks to reduce re-renders: accumulate text, flush every 50ms.
          //
          // Tool and download data never enter `message.content` — they flow
          // into `message.toolCalls` and `message.s3Downloads` sidecars. This
          // is what breaks the hallucination loop: on subsequent turns the
          // content we replay to the model contains only prose, so there is
          // no `<details>` / `__S3_DOWNLOAD__` format for it to mimic.
          let pendingText = "";
          let flushTimer: ReturnType<typeof setTimeout> | null = null;

          // Blocks mirror the streaming order (text / tool_call / text / ...).
          // Maintained in parallel with the flat `content` + `toolCalls` fields
          // so the existing consumers (history replay, export, fallback
          // render) don't need to know about blocks. The invariant is:
          // concat of every text block's `text` === content.
          const appendTextBlock = (text: string) => {
            if (!text) return;
            set((s) => setMessagesFor(s, sendingAgentKey, (msgs) =>
              msgs.map((m) => {
                if (m.id !== assistantMsg.id) return m;
                const blocks = [...(m.blocks || [])];
                const last = blocks[blocks.length - 1];
                if (last?.kind === "text") {
                  // Mutate a shallow copy so React sees the reference change.
                  blocks[blocks.length - 1] = { kind: "text", text: last.text + text };
                } else {
                  blocks.push({ kind: "text", text });
                }
                return { ...m, content: m.content + text, blocks };
              }),
            ));
          };

          const flushPending = () => {
            if (!pendingText) return;
            const text = pendingText;
            pendingText = "";
            appendTextBlock(text);
          };

          const scheduleFlush = () => {
            if (!flushTimer) {
              flushTimer = setTimeout(() => {
                flushTimer = null;
                flushPending();
              }, 50);
            }
          };

          const appendToolCall = (call: ToolCallRecord) => {
            set((s) => setMessagesFor(s, sendingAgentKey, (msgs) =>
              msgs.map((m) => m.id === assistantMsg.id
                ? {
                    ...m,
                    toolCalls: [...(m.toolCalls || []), call],
                    blocks: [...(m.blocks || []), { kind: "tool_call", call }],
                  }
                : m,
              ),
            ));
          };

          const appendDownloads = (items: S3Download[]) => {
            if (items.length === 0) return;
            set((s) => setMessagesFor(s, sendingAgentKey, (msgs) =>
              msgs.map((m) => {
                if (m.id !== assistantMsg.id) return m;
                const seen = new Set((m.s3Downloads || []).map((d) => d.key));
                const next = [...(m.s3Downloads || [])];
                for (const it of items) {
                  if (!seen.has(it.key)) { seen.add(it.key); next.push(it); }
                }
                return { ...m, s3Downloads: next };
              }),
            ));
          };

          // Scan prose text for `__S3_DOWNLOAD__:key:filename` markers. Returns
          // cleaned text (markers stripped) + any complete downloads found.
          const downloadRe = /__S3_DOWNLOAD__:([^:\s"}\]]+):([^\s"}\]]+)/g;
          const extractDownloads = (text: string): { clean: string; downloads: S3Download[] } => {
            const downloads: S3Download[] = [];
            const clean = text.replace(downloadRe, (_m, key: string, filename: string) => {
              downloads.push({ key, filename });
              return "";
            });
            return { clean, downloads };
          };

          let toolBuffer = "";    // Buffer for incomplete __tool JSON markers across chunks
          let textCarry = "";     // Buffer for text that may contain a partial __S3_DOWNLOAD__ marker

          for await (const chunk of stream) {
            if (signal.aborted) break;

            // 1a. Auto-continue control frame: Meta-Agent's supervisor
            //     emits `{"__auto_continue": N, "max": M}` as its own
            //     top-level SSE frame before re-prompting Kiro after a
            //     premature end_turn. parseSSEStream in agentcore-client
            //     has already filtered it to standalone frames (not
            //     prose-embedded quotes), so we recognize via a simple
            //     startsWith + JSON.parse. Regex-scanning the chunk body
            //     was fragile: a user quoting the JSON back would have
            //     triggered the badge.
            let cleanedAutoCont = chunk;
            const trimmedChunk = chunk.trimStart();
            if (trimmedChunk.startsWith('{"__auto_continue"')) {
              try {
                const parsed = JSON.parse(trimmedChunk);
                if (typeof parsed.__auto_continue === "number" && typeof parsed.max === "number") {
                  set((s) => setFlagFor(s, "autoContinueByAgent", sendingAgentKey, {
                    n: parsed.__auto_continue,
                    max: parsed.max,
                  }));
                  cleanedAutoCont = "";  // consume the whole chunk
                }
              } catch {
                // malformed — let it fall through as normal content
              }
            } else if (chunk.length > 0 && get().autoContinueByAgent[sendingAgentKey]) {
              // First real content after an auto-continue announcement —
              // clear the badge so the user sees normal streaming again.
              set((s) => setFlagFor(s, "autoContinueByAgent", sendingAgentKey, null));
            }
            if (!cleanedAutoCont) continue;  // nothing left to process this chunk

            // 1. Extract complete __tool JSON markers from the buffer.
            const toolJsonRe = /\{"__tool"[^}]*\}/g;
            toolBuffer += cleanedAutoCont;
            let textAfterTools = "";
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

            // Hold back any incomplete trailing __tool marker.
            const lastOpenBrace = toolBuffer.lastIndexOf('{"__tool"');
            if (lastOpenBrace >= lastMatchEnd) {
              textAfterTools = toolBuffer.slice(0, lastOpenBrace).replace(toolJsonRe, "");
              toolBuffer = toolBuffer.slice(lastOpenBrace);
            } else {
              textAfterTools = toolBuffer.replace(toolJsonRe, "");
              toolBuffer = "";
            }

            // 2. Apply tool markers to the sidecar field.
            for (const m of markers) {
              if (m.type === "start") {
                set((s) => setFlagFor(s, "activeToolByAgent", sendingAgentKey, m.name));
              } else if (m.type === "result") {
                let inp = "", out = "";
                try { inp = m.input ? new TextDecoder().decode(Uint8Array.from(atob(m.input), c => c.charCodeAt(0))) : ""; } catch { inp = m.input || ""; }
                try { out = m.output ? new TextDecoder().decode(Uint8Array.from(atob(m.output), c => c.charCodeAt(0))) : ""; } catch { out = m.output || ""; }
                const isSvg = !!out && out.trimStart().startsWith("<svg");
                // Flush buffered prose before pushing the tool block so
                // the tool lands AFTER the text the model said just before
                // calling it, not before.
                flushPending();
                appendToolCall({
                  id: crypto.randomUUID(),
                  name: m.name,
                  input: inp,
                  output: out,
                  isSvg,
                });
                // upload_to_s3 (and friends) return `__S3_DOWNLOAD__:key:filename`
                // inside tool output. The model often doesn't echo it back
                // into prose, so the download button never appears if we
                // only scan message.content. Extract here too.
                const toolDownloads: S3Download[] = [];
                for (const mt of out.matchAll(/__S3_DOWNLOAD__:([^:\s"}\]]+):([^\s"}\]]+)/g)) {
                  toolDownloads.push({ key: mt[1], filename: mt[2] });
                }
                // generate_image / create_storyboard return JSON with
                // s3_key fields. Storyboard outputs routinely exceed the
                // 5KB tool-output cap (long frame descriptions + presigned
                // URLs) and arrive truncated, so JSON.parse is unreliable —
                // scan for "s3_key" values with a regex instead.
                if (m.name === "generate_image" || m.name === "create_storyboard") {
                  const seen = new Set(toolDownloads.map((d) => d.key));
                  for (const mt of out.matchAll(/"s3_key"\s*:\s*"([^"]+)"/g)) {
                    const key = mt[1];
                    if (seen.has(key)) continue;
                    seen.add(key);
                    const fname = key.split("/").pop() || "image.png";
                    toolDownloads.push({ key, filename: fname });
                  }
                }
                if (toolDownloads.length > 0) appendDownloads(toolDownloads);
              } else if (m.type === "end") {
                set((s) => setFlagFor(s, "activeToolByAgent", sendingAgentKey, null));
              }
            }

            // 3. Process prose text. A `__S3_DOWNLOAD__:key:filename` marker
            //    may be split across chunk boundaries, either mid-prefix
            //    ("__S3_DO" | "WNLOAD__:...") or mid-filename. Hold back
            //    from the earliest position that could still be part of an
            //    in-flight marker; emit everything before it.
            textCarry += textAfterTools;
            const MARKER = "__S3_DOWNLOAD__";
            let holdFrom = -1;

            // Case A: a complete `__S3_DOWNLOAD__` is present but the
            // filename portion may still be streaming — hold the full
            // marker until we see a terminator.
            const lastFull = textCarry.lastIndexOf(MARKER);
            if (lastFull >= 0 && !/[\s"}\]]/.test(textCarry.slice(lastFull + MARKER.length))) {
              holdFrom = lastFull;
            }

            // Case B: only a prefix of the marker arrived at the tail
            // (e.g. "__S3_DO"). Hold it.
            if (holdFrom < 0) {
              for (let len = MARKER.length - 1; len >= 2; len--) {
                if (textCarry.endsWith(MARKER.slice(0, len))) {
                  holdFrom = textCarry.length - len;
                  break;
                }
              }
            }

            let emittable: string;
            if (holdFrom >= 0) {
              emittable = textCarry.slice(0, holdFrom);
              textCarry = textCarry.slice(holdFrom);
            } else {
              emittable = textCarry;
              textCarry = "";
            }

            if (emittable) {
              const { clean, downloads } = extractDownloads(emittable);
              if (downloads.length > 0) {
                flushPending();
                appendDownloads(downloads);
              }
              if (clean) {
                pendingText += clean;
                scheduleFlush();
              }
            }
          }

          // Drain any leftover buffers at end of stream.
          if (toolBuffer) {
            textCarry += toolBuffer;
            toolBuffer = "";
          }
          if (textCarry) {
            const { clean, downloads } = extractDownloads(textCarry);
            if (downloads.length > 0) appendDownloads(downloads);
            if (clean) pendingText += clean;
            textCarry = "";
          }

          if (flushTimer) clearTimeout(flushTimer);
          flushPending();
        } catch (err) {
          if (!signal.aborted) {
            // Append an error block instead of overwriting content.
            // Overwriting threw away every text chunk + tool-call that
            // had already streamed in — users saw a partial answer,
            // then hit Copy and got only "错误: network error", losing
            // everything the agent had said. Preserving the received
            // content + adding a trailing error block keeps the work
            // visible and lets Copy still return the real answer.
            const errText = i18next.t("chat.errorPrefix", "Error") +
              `: ${err instanceof Error ? err.message : "Unknown error"}`;
            set((s) => setMessagesFor(s, sendingAgentKey, (msgs) =>
              msgs.map((m) => m.id === assistantMsg.id
                ? { ...m, blocks: [...(m.blocks || []), { kind: "error", text: errText }] }
                : m,
              ),
            ));
          }
        } finally {
          if (_abortControllersByAgent.get(sendingAgentKey) === controller) {
            _abortControllersByAgent.delete(sendingAgentKey);
          }
          set((s) => ({
            ...setFlagFor(s, "streamingByAgent", sendingAgentKey, false),
            ...setFlagFor(s, "statusByAgent", sendingAgentKey, null),
            ...setFlagFor(s, "activeToolByAgent", sendingAgentKey, null),
            ...setFlagFor(s, "autoContinueByAgent", sendingAgentKey, null),
          }));
          // Snapshot the just-completed turn into a session and push to S3.
          // Uses the sending agent's key, not currentAgentId, so the save
          // targets the right agent even if the user navigated away.
          _saveCurrentSessionAndSync(get(), set, sendingAgentKey);
        }
      },

      clearMessages: () => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        _saveCurrentSessionAndSync(state, set);
        set((s) => ({
          ...setMessagesFor(s, key, () => []),
          ...setFlagFor(s, "sessionIdByAgent", key, undefined),
          ...setFlagFor(s, "activeSessionByAgent", key, null),
          ...setFlagFor(s, "statusByAgent", key, null),
          ...setFlagFor(s, "autoContinueByAgent", key, null),
        }));
      },

      cancelStreaming: () => {
        // Cancel only the current agent's stream (the one the user is
        // looking at). Other agents keep streaming.
        const key = agentKey(get().currentAgentId);
        const controller = _abortControllersByAgent.get(key);
        if (controller) {
          controller.abort();
          _abortControllersByAgent.delete(key);
        }
        set((s) => ({
          ...setFlagFor(s, "streamingByAgent", key, false),
          ...setFlagFor(s, "statusByAgent", key, null),
          // Belt-and-suspenders: sendMessage's `finally` also clears
          // this, but if a future refactor skips that path the badge
          // should still come down the moment the user hits cancel.
          ...setFlagFor(s, "autoContinueByAgent", key, null),
        }));
      },

      regenerateLastMessage: async () => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        const messages = state.messagesByAgent[key] || [];
        if (state.streamingByAgent[key]) return;

        const lastUserIdx = messages.findLastIndex((m) => m.role === "user");
        if (lastUserIdx === -1) return;

        const lastUserMsg = messages[lastUserIdx];
        const content = lastUserMsg.content;
        const images = lastUserMsg.images;
        const attachments = lastUserMsg.attachments;

        const trimmed = messages.slice(0, lastUserIdx);
        set((s) => setMessagesFor(s, key, () => trimmed));

        const modelId = state.selectedModelByAgent[key] || undefined;
        await get().sendMessage(content, images, modelId, attachments);
      },

      editAndResend: async (messageId: string, newContent: string) => {
        const state = get();
        const key = agentKey(state.currentAgentId);
        const messages = state.messagesByAgent[key] || [];
        if (state.streamingByAgent[key]) return;

        const msgIdx = messages.findIndex((m) => m.id === messageId);
        if (msgIdx === -1) return;

        const trimmed = messages.slice(0, msgIdx);
        const images = messages[msgIdx].images;
        const attachments = messages[msgIdx].attachments;
        set((s) => setMessagesFor(s, key, () => trimmed));

        const modelId = state.selectedModelByAgent[key] || undefined;
        await get().sendMessage(newContent, images, modelId, attachments);
      },
    }),
    {
      name: "agent-studio-chat",
      version: 3,
      partialize: (state) => _sanitizeForPersist(state),
      migrate: (persisted, _fromVersion) =>
        _migrateToV3((persisted || {}) as LegacyPersistedState) as ChatState,
    },
  ),
);

/**
 * Reset chat state on workspace switch.
 *
 * Persisted sessions/currentAgentId/lastActiveSessionByAgent reference agent
 * IDs scoped to a single workspace — after switching workspaces those IDs are
 * stale and would render as broken links. Clear both the in-memory store and
 * the persisted localStorage copy. When the user returns to the original
 * workspace the store will hydrate from the cloud via hydrateSessionsFromCloud.
 *
 * Intentionally does NOT touch `agent-studio-ui` (workspace-agnostic) or
 * `agent-studio-workspace-id` (identifies the workspace itself).
 */
export function resetChatForWorkspaceSwitch(): void {
  // Abort every in-flight stream before wiping state.
  for (const c of _abortControllersByAgent.values()) {
    try { c.abort(); } catch { /* ignore */ }
  }
  _abortControllersByAgent.clear();
  resetCloudHydrationCache();
  useChatStore.setState(_chatInitialState);
  try {
    useChatStore.persist.clearStorage();
  } catch {
    // clearStorage can throw if storage is unavailable — safe to ignore.
  }
}

// ── Cloud sync (S3-backed, per-user within workspace) ───────────────────
//
// Sessions live in S3 at chat/{ws}/{user}/{agentKey}/sessions/{id}.json.
// The Lambda derives {user} from the JWT so the client cannot address
// another user's folder even if the localStorage copy is tampered with.
//
// The design is intentionally simple: localStorage stays as a warm cache
// (offline resilience + zero-latency first paint); the cloud is authority.
// On agent switch we list + pull; after each turn we PUT the active
// session (debounced per-session to coalesce streaming updates).

const _cloudSaveTimers = new Map<string, ReturnType<typeof setTimeout>>();
// Guard against re-entrant hydrates on rapid agent switches.
const _hydratedAgentKeys = new Set<string>();

function _queueCloudSave(session: ChatSession): void {
  const existing = _cloudSaveTimers.get(session.id);
  if (existing) clearTimeout(existing);
  const t = setTimeout(() => {
    _cloudSaveTimers.delete(session.id);
    // Fire-and-forget. The helper swallows errors; localStorage still has
    // the latest state so nothing is lost if the PUT fails.
    void putChatSession(session.agentKey, session.id, session);
  }, 1500);
  _cloudSaveTimers.set(session.id, t);
}

function _saveCurrentSessionAndSync(
  state: ChatState,
  set: (partial: Partial<ChatState> | ((s: ChatState) => Partial<ChatState>)) => void,
  explicitKey?: string,
): void {
  _saveCurrentSession(state, set, explicitKey);
  const after = useChatStore.getState();
  const key = explicitKey ?? agentKey(after.currentAgentId);
  const activeId = after.activeSessionByAgent[key];
  if (!activeId) return;
  const sess = after.sessions.find((s) => s.id === activeId);
  if (sess) _queueCloudSave(sess);
}

/**
 * Pull the session list for an agent from the cloud and merge it into the
 * in-memory store. Cloud is the source of truth — server records overwrite
 * matching local ids by session.updatedAt.
 *
 * Called on first switch to an agent in this browser session. Subsequent
 * switches read from in-memory state to avoid flicker. Failures are silent
 * and leave local state intact so the UI degrades to "localStorage only".
 */
export async function hydrateSessionsFromCloud(agentIdOrKey: string | null): Promise<void> {
  const key = agentKey(agentIdOrKey);
  if (_hydratedAgentKeys.has(key)) return;
  _hydratedAgentKeys.add(key);

  let summaries;
  try {
    summaries = await listChatSessions(key);
  } catch {
    _hydratedAgentKeys.delete(key);
    return;
  }

  if (!summaries || summaries.length === 0) return;

  // Pull full session bodies sequentially to avoid thundering a cold Lambda.
  // 30-session cap means this is at most a few seconds in the worst case.
  const fetched: ChatSession[] = [];
  for (const summary of summaries) {
    const body = await getChatSession<ChatSession>(key, summary.id);
    if (body && Array.isArray(body.messages)) fetched.push(body);
  }
  if (fetched.length === 0) return;

  useChatStore.setState((s) => {
    const byId = new Map<string, ChatSession>();
    for (const sess of s.sessions) byId.set(sess.id, sess);
    for (const sess of fetched) {
      const existing = byId.get(sess.id);
      if (!existing || (sess.updatedAt || 0) >= (existing.updatedAt || 0)) {
        byId.set(sess.id, sess);
      }
    }
    return { sessions: Array.from(byId.values()) };
  });
}

/**
 * Drop in-memory hydration gate. Called after a workspace switch so the next
 * agent visit re-pulls from the (new workspace's) cloud.
 */
export function resetCloudHydrationCache(): void {
  _hydratedAgentKeys.clear();
  for (const t of _cloudSaveTimers.values()) clearTimeout(t);
  _cloudSaveTimers.clear();
}

/**
 * Save the current agent's message buffer as a session.
 *
 * Reads from `state.messagesByAgent[currentKey]` rather than a top-level
 * `messages` field. No-op if the buffer is empty.
 */
function _saveCurrentSession(
  state: ChatState,
  set: (partial: Partial<ChatState> | ((s: ChatState) => Partial<ChatState>)) => void,
  explicitKey?: string,
) {
  const key = explicitKey ?? agentKey(state.currentAgentId);
  const buffer = state.messagesByAgent[key] || [];
  const msgs = buffer.filter(
    (m) => m.content || m.toolCalls?.length || m.s3Downloads?.length,
  );
  if (msgs.length === 0) return;

  const now = Date.now();
  const activeId = state.activeSessionByAgent[key] ?? null;
  const modelId = state.selectedModelByAgent[key] || undefined;

  if (activeId) {
    set((s) => ({
      sessions: s.sessions.map((sess) =>
        sess.id === activeId
          ? { ...sess, messages: msgs, title: deriveTitle(msgs), modelId, updatedAt: now }
          : sess,
      ),
    }));
  } else {
    const newSession: ChatSession = {
      id: crypto.randomUUID(),
      agentKey: key,
      title: deriveTitle(msgs),
      messages: msgs,
      modelId,
      createdAt: now,
      updatedAt: now,
    };
    set((s) => ({
      sessions: [...s.sessions, newSession],
      activeSessionByAgent: { ...s.activeSessionByAgent, [key]: newSession.id },
    }));
  }
}

// ── Selector hooks — preferred way for UI to read per-agent slices ──────

/**
 * Read-only view of the chat slice for a specific agent (or the current
 * agent if not specified).
 *
 * Components that render a specific agent (e.g. ChatPanel reading `agentId`
 * from the URL) should **always pass that id explicitly** — otherwise there
 * is a 1-frame window during navigation where `currentAgentId` in the store
 * lags the URL (because `switchAgent` runs in a useEffect after commit) and
 * the selector briefly returns the previous agent's state. That produced
 * the "calling load_skill" label flashing on the next agent's chat page.
 */
// Stable empty array reference so selectors returning "no messages" don't
// yield a new `[]` each call (which would defeat shallow equality).
const _EMPTY_MESSAGES: readonly Message[] = Object.freeze([]);

export function useCurrentAgentChat(agentId?: string | null) {
  // useShallow prevents an infinite render loop: zustand v5 compares
  // selector output with Object.is by default, but this selector
  // constructs a fresh object each call, so every unrelated store tick
  // would re-render + re-create + re-render. useShallow switches the
  // comparator to a shallow structural equality, returning the previous
  // reference when no field actually changed.
  return useChatStore(
    useShallow((s) => {
      const key = agentKey(agentId !== undefined ? agentId : s.currentAgentId);
      return {
        messages: s.messagesByAgent[key] || (_EMPTY_MESSAGES as Message[]),
        isStreaming: !!s.streamingByAgent[key],
        statusText: s.statusByAgent[key] ?? null,
        activeTool: s.activeToolByAgent[key] ?? null,
        autoContinue: s.autoContinueByAgent[key] ?? null,
        sessionId: s.sessionIdByAgent[key],
        activeSessionId: s.activeSessionByAgent[key] ?? null,
        selectedModelId: s.selectedModelByAgent[key] ?? null,
      };
    }),
  );
}
