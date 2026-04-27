import { create } from "zustand";
import {
  getMcpCatalog,
  getMcpStatus,
  type McpCatalog,
  type McpCatalogTarget,
  type McpRuntimeEntry,
  type McpRuntimeStatus,
} from "../lib/api-client";

/**
 * Per-workspace MCP runtime store (spec §8.3).
 *
 * Holds the catalog for the current workspace + per-target poll timers
 * for in-flight enables/disables/upgrades. Exponential backoff
 * 5s → 10s → 20s → 30s capped. Polls stop on terminal state, page
 * navigation, or 10-min wall clock.
 *
 * No cross-tab dedupe (BroadcastChannel was dropped after review).
 * Two tabs polling at 30s costs negligible extra traffic.
 */

type PollHandle = {
  timer: ReturnType<typeof setTimeout>;
  startedAt: number;
  nextDelayMs: number;
};

const POLL_SCHEDULE = [5_000, 10_000, 20_000, 30_000] as const;
const MAX_POLL_MS = 10 * 60 * 1_000;
const TERMINAL: McpRuntimeStatus[] = ["READY", "ACTIVE", "FAILED", "DELETED"];

export interface McpStoreState {
  // Scope
  workspaceId: string | null;

  // Data
  catalog: McpCatalog | null;
  loading: boolean;
  error: string | null;

  // In-flight polling (target → handle)
  polls: Record<string, PollHandle | undefined>;

  // Actions
  setWorkspace: (wsId: string | null) => void;
  refreshCatalog: () => Promise<void>;
  startPoll: (target: string) => void;
  stopPoll: (target: string) => void;
  stopAllPolls: () => void;
  updateTargetEntry: (target: string, entry: McpRuntimeEntry) => void;
}

function isTerminal(status: McpRuntimeStatus | undefined): boolean {
  return status !== undefined && TERMINAL.includes(status);
}

export const useMcpStore = create<McpStoreState>((set, get) => ({
  workspaceId: null,
  catalog: null,
  loading: false,
  error: null,
  polls: {},

  setWorkspace: (wsId) => {
    const prev = get().workspaceId;
    if (prev === wsId) return;
    // Cancel in-flight polls when workspace changes
    get().stopAllPolls();
    set({ workspaceId: wsId, catalog: null, error: null });
  },

  refreshCatalog: async () => {
    const ws = get().workspaceId;
    if (!ws) return;
    set({ loading: true, error: null });
    try {
      const catalog = await getMcpCatalog(ws);
      set({ catalog, loading: false });
      // Re-hydrate polling: any target in non-terminal → start poll if not already
      const inflight = catalog.targets.filter(
        (t) => t.enabled && t.runtime && !isTerminal(t.runtime.status),
      );
      const polls = get().polls;
      for (const t of inflight) {
        if (!polls[t.name]) {
          get().startPoll(t.name);
        }
      }
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  startPoll: (target) => {
    const ws = get().workspaceId;
    if (!ws) return;
    // Cancel any existing poll for this target
    get().stopPoll(target);

    const startedAt = Date.now();
    let step = 0;

    const tick = async () => {
      const state = get();
      if (state.workspaceId !== ws) return; // workspace changed
      if (Date.now() - startedAt > MAX_POLL_MS) {
        // Hit 10-min cap; leave the entry as-is and stop
        get().stopPoll(target);
        return;
      }
      try {
        const entry = await getMcpStatus(ws, target);
        get().updateTargetEntry(target, entry);
        if (isTerminal(entry.status)) {
          get().stopPoll(target);
          return;
        }
      } catch (e) {
        // Transient network error — keep polling; log to console only
        // so the UI doesn't flicker with red toasts
        // eslint-disable-next-line no-console
        console.warn(`[mcp-store] poll ${target} failed:`, e);
      }
      step = Math.min(step + 1, POLL_SCHEDULE.length - 1);
      const delay = POLL_SCHEDULE[step];
      const timer = setTimeout(tick, delay);
      set((s) => ({
        polls: { ...s.polls, [target]: { timer, startedAt, nextDelayMs: delay } },
      }));
    };

    const firstDelay = POLL_SCHEDULE[0];
    const timer = setTimeout(tick, firstDelay);
    set((s) => ({
      polls: {
        ...s.polls,
        [target]: { timer, startedAt, nextDelayMs: firstDelay },
      },
    }));
  },

  stopPoll: (target) => {
    set((s) => {
      const handle = s.polls[target];
      if (handle) clearTimeout(handle.timer);
      const { [target]: _, ...rest } = s.polls;
      return { polls: rest };
    });
  },

  stopAllPolls: () => {
    const polls = get().polls;
    for (const key of Object.keys(polls)) {
      const h = polls[key];
      if (h) clearTimeout(h.timer);
    }
    set({ polls: {} });
  },

  updateTargetEntry: (target, entry) => {
    set((s) => {
      if (!s.catalog) return s;
      const nextTargets: McpCatalogTarget[] = s.catalog.targets.map((t) =>
        t.name === target ? { ...t, enabled: true, runtime: entry } : t,
      );
      return { catalog: { ...s.catalog, targets: nextTargets } };
    });
  },
}));
