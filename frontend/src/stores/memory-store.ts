import { create } from "zustand";
import {
  listMyMemories,
  loadMoreMemories,
  deleteMyMemory,
  forgetAllMemories,
  type MemoryRecord,
  type MemoryStrategy,
} from "../lib/api-client";

interface Section {
  records: MemoryRecord[];
  nextToken: string | null;
  loadingMore: boolean;
}

interface Bucket {
  preferences: Section;
  facts: Section;
  summaries: Section;
  episodes: Section;
  loading: boolean;
}

interface MemoryState {
  byAgent: Record<string, Bucket>;
  fetchMemories: (workspaceId: string, agentId: string) => Promise<void>;
  loadMore: (workspaceId: string, agentId: string, strategy: MemoryStrategy) => Promise<void>;
  deleteRecord: (workspaceId: string, agentId: string, recordId: string, strategy: MemoryStrategy) => Promise<void>;
  forgetAll: (workspaceId: string, agentId: string) => Promise<{ deleted: number; partial: boolean }>;
}

const emptySection = (): Section => ({ records: [], nextToken: null, loadingMore: false });
const emptyBucket = (): Bucket => ({
  preferences: emptySection(),
  facts: emptySection(),
  summaries: emptySection(),
  episodes: emptySection(),
  loading: false,
});

export const useMemoryStore = create<MemoryState>((set, get) => ({
  byAgent: {},

  fetchMemories: async (workspaceId, agentId) => {
    set((s) => ({
      byAgent: { ...s.byAgent, [agentId]: { ...emptyBucket(), loading: true } },
    }));
    try {
      const bundle = await listMyMemories(workspaceId, agentId);
      set((s) => ({
        byAgent: {
          ...s.byAgent,
          [agentId]: {
            preferences: { ...bundle.preferences, loadingMore: false },
            facts: { ...bundle.facts, loadingMore: false },
            summaries: { ...bundle.summaries, loadingMore: false },
            episodes: { ...bundle.episodes, loadingMore: false },
            loading: false,
          },
        },
      }));
    } catch {
      set((s) => ({
        byAgent: { ...s.byAgent, [agentId]: emptyBucket() },
      }));
    }
  },

  loadMore: async (workspaceId, agentId, strategy) => {
    const bucket = get().byAgent[agentId];
    if (!bucket) return;
    const section = bucket[strategy];
    if (!section.nextToken) return;

    set((s) => ({
      byAgent: {
        ...s.byAgent,
        [agentId]: { ...bucket, [strategy]: { ...section, loadingMore: true } },
      },
    }));
    try {
      const page = await loadMoreMemories(workspaceId, agentId, strategy, section.nextToken);
      set((s) => {
        const b = s.byAgent[agentId];
        if (!b) return s;
        const sec = b[strategy];
        return {
          byAgent: {
            ...s.byAgent,
            [agentId]: {
              ...b,
              [strategy]: {
                records: [...sec.records, ...page.records],
                nextToken: page.nextToken,
                loadingMore: false,
              },
            },
          },
        };
      });
    } catch {
      set((s) => {
        const b = s.byAgent[agentId];
        if (!b) return s;
        return {
          byAgent: {
            ...s.byAgent,
            [agentId]: { ...b, [strategy]: { ...b[strategy], loadingMore: false } },
          },
        };
      });
    }
  },

  deleteRecord: async (workspaceId, agentId, recordId, strategy) => {
    await deleteMyMemory(workspaceId, agentId, recordId, strategy);
    set((s) => {
      const b = s.byAgent[agentId];
      if (!b) return s;
      const sec = b[strategy];
      return {
        byAgent: {
          ...s.byAgent,
          [agentId]: {
            ...b,
            [strategy]: { ...sec, records: sec.records.filter((r) => r.id !== recordId) },
          },
        },
      };
    });
  },

  forgetAll: async (workspaceId, agentId) => {
    const result = await forgetAllMemories(workspaceId, agentId);
    set((s) => ({
      byAgent: { ...s.byAgent, [agentId]: emptyBucket() },
    }));
    return result;
  },
}));
