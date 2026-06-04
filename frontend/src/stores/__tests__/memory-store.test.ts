import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api-client", () => ({
  listMyMemories: vi.fn(),
  loadMoreMemories: vi.fn(),
  deleteMyMemory: vi.fn(),
  forgetAllMemories: vi.fn(),
}));

import { useMemoryStore } from "../memory-store";
import {
  listMyMemories,
  loadMoreMemories,
  deleteMyMemory,
  forgetAllMemories,
} from "../../lib/api-client";

const mockList = vi.mocked(listMyMemories);
const mockLoadMore = vi.mocked(loadMoreMemories);
const mockDelete = vi.mocked(deleteMyMemory);
const mockForget = vi.mocked(forgetAllMemories);

const rec = (id: string) => ({
  id,
  content: { text: `record ${id}` },
  createdAt: 1700000000,
  namespace: "ns",
});

const bundle = (overrides: Partial<{
  preferences: { records: ReturnType<typeof rec>[]; nextToken: string | null };
  facts: { records: ReturnType<typeof rec>[]; nextToken: string | null };
  summaries: { records: ReturnType<typeof rec>[]; nextToken: string | null };
  episodes: { records: ReturnType<typeof rec>[]; nextToken: string | null };
}> = {}) => ({
  preferences: { records: [], nextToken: null, ...overrides.preferences },
  facts: { records: [], nextToken: null, ...overrides.facts },
  summaries: { records: [], nextToken: null, ...overrides.summaries },
  episodes: { records: [], nextToken: null, ...overrides.episodes },
});

beforeEach(() => {
  vi.clearAllMocks();
  useMemoryStore.setState({ byAgent: {} });
});

describe("memory-store", () => {
  describe("fetchMemories", () => {
    it("populates the agent's bucket from a successful response", async () => {
      mockList.mockResolvedValue(
        bundle({
          preferences: { records: [rec("p1")], nextToken: "tok-p" },
          facts: { records: [rec("f1")], nextToken: null },
        }),
      );

      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      const b = useMemoryStore.getState().byAgent["agent-1"];

      expect(b).toBeTruthy();
      expect(b.loading).toBe(false);
      expect(b.preferences.records).toHaveLength(1);
      expect(b.preferences.nextToken).toBe("tok-p");
      expect(b.preferences.loadingMore).toBe(false);
      expect(b.facts.records).toHaveLength(1);
      expect(mockList).toHaveBeenCalledWith("ws-1", "agent-1");
    });

    it("resets the bucket to empty on failure", async () => {
      mockList.mockRejectedValue(new Error("net"));
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      const b = useMemoryStore.getState().byAgent["agent-1"];

      expect(b).toBeTruthy();
      expect(b.loading).toBe(false);
      expect(b.preferences.records).toEqual([]);
      expect(b.facts.records).toEqual([]);
    });

    it("clears stale records from a previous successful fetch on failure", async () => {
      // Seed a populated bucket.
      mockList.mockResolvedValueOnce(
        bundle({ preferences: { records: [rec("p1")], nextToken: null } }),
      );
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      expect(useMemoryStore.getState().byAgent["agent-1"].preferences.records).toHaveLength(1);

      mockList.mockRejectedValueOnce(new Error("boom"));
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      expect(useMemoryStore.getState().byAgent["agent-1"].preferences.records).toEqual([]);
    });
  });

  describe("loadMore", () => {
    it("does nothing when the bucket is missing", async () => {
      await useMemoryStore.getState().loadMore("ws-1", "agent-x", "preferences");
      expect(mockLoadMore).not.toHaveBeenCalled();
    });

    it("does nothing when nextToken is null", async () => {
      mockList.mockResolvedValue(bundle());
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      await useMemoryStore.getState().loadMore("ws-1", "agent-1", "preferences");
      expect(mockLoadMore).not.toHaveBeenCalled();
    });

    it("appends a page of records and updates the nextToken", async () => {
      mockList.mockResolvedValue(
        bundle({ preferences: { records: [rec("p1")], nextToken: "tok-1" } }),
      );
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");

      mockLoadMore.mockResolvedValue({
        records: [rec("p2"), rec("p3")],
        nextToken: "tok-2",
      });
      await useMemoryStore.getState().loadMore("ws-1", "agent-1", "preferences");

      const sec = useMemoryStore.getState().byAgent["agent-1"].preferences;
      expect(sec.records.map((r) => r.id)).toEqual(["p1", "p2", "p3"]);
      expect(sec.nextToken).toBe("tok-2");
      expect(sec.loadingMore).toBe(false);
      expect(mockLoadMore).toHaveBeenCalledWith("ws-1", "agent-1", "preferences", "tok-1");
    });

    it("clears loadingMore when the page request fails", async () => {
      mockList.mockResolvedValue(
        bundle({ facts: { records: [rec("f1")], nextToken: "tok-f" } }),
      );
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");

      mockLoadMore.mockRejectedValue(new Error("net"));
      await useMemoryStore.getState().loadMore("ws-1", "agent-1", "facts");

      const sec = useMemoryStore.getState().byAgent["agent-1"].facts;
      expect(sec.loadingMore).toBe(false);
      // Records and token unchanged.
      expect(sec.records.map((r) => r.id)).toEqual(["f1"]);
      expect(sec.nextToken).toBe("tok-f");
    });
  });

  describe("deleteRecord", () => {
    it("calls the API and removes the record locally", async () => {
      mockList.mockResolvedValue(
        bundle({
          summaries: {
            records: [rec("s1"), rec("s2"), rec("s3")],
            nextToken: null,
          },
        }),
      );
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");

      mockDelete.mockResolvedValue({ deleted: "s2" });
      await useMemoryStore.getState().deleteRecord("ws-1", "agent-1", "s2", "summaries");

      const remaining = useMemoryStore
        .getState()
        .byAgent["agent-1"].summaries.records.map((r) => r.id);
      expect(remaining).toEqual(["s1", "s3"]);
      expect(mockDelete).toHaveBeenCalledWith("ws-1", "agent-1", "s2", "summaries");
    });

    it("is a no-op locally when the agent has no bucket yet", async () => {
      mockDelete.mockResolvedValue({ deleted: "s1" });
      await useMemoryStore
        .getState()
        .deleteRecord("ws-1", "agent-x", "s1", "summaries");
      expect(useMemoryStore.getState().byAgent["agent-x"]).toBeUndefined();
      expect(mockDelete).toHaveBeenCalled();
    });
  });

  describe("forgetAll", () => {
    it("calls the API, resets the bucket, and returns the count", async () => {
      mockList.mockResolvedValue(
        bundle({ episodes: { records: [rec("e1")], nextToken: null } }),
      );
      await useMemoryStore.getState().fetchMemories("ws-1", "agent-1");
      expect(useMemoryStore.getState().byAgent["agent-1"].episodes.records).toHaveLength(1);

      mockForget.mockResolvedValue({ deleted: 7, partial: false });
      const result = await useMemoryStore.getState().forgetAll("ws-1", "agent-1");

      expect(result).toEqual({ deleted: 7, partial: false });
      const b = useMemoryStore.getState().byAgent["agent-1"];
      expect(b.episodes.records).toEqual([]);
      expect(b.preferences.records).toEqual([]);
      expect(mockForget).toHaveBeenCalledWith("ws-1", "agent-1");
    });
  });
});
