import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  saveDraft,
  loadDraft,
  clearDraft,
  createDebouncedSaver,
  formatDraftAge,
} from "../draft-autosave";

describe("draft-autosave", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("round-trips a draft", () => {
    saveDraft("k", { foo: "bar", n: 42 });
    expect(loadDraft("k")).toEqual({ foo: "bar", n: 42 });
  });

  it("returns null when no draft exists", () => {
    expect(loadDraft("missing")).toBeNull();
  });

  it("drops drafts older than one week", () => {
    saveDraft("k", "hello");
    // Fast-forward the clock past the 7-day TTL
    vi.setSystemTime(Date.now() + 8 * 24 * 60 * 60 * 1000);
    expect(loadDraft("k")).toBeNull();
    // Expired entry should have been cleaned up
    expect(localStorage.getItem("k")).toBeNull();
  });

  it("drops corrupted envelopes", () => {
    localStorage.setItem("k", "not json");
    expect(loadDraft("k")).toBeNull();
    expect(localStorage.getItem("k")).toBeNull();
  });

  it("rejects envelopes without the expected version", () => {
    localStorage.setItem("k", JSON.stringify({ v: 99, ts: Date.now(), data: {} }));
    expect(loadDraft("k")).toBeNull();
  });

  it("refuses oversized drafts", () => {
    const big = "x".repeat(600 * 1024);
    saveDraft("k", big);
    expect(loadDraft("k")).toBeNull();
  });

  it("clearDraft wipes a key", () => {
    saveDraft("k", "v");
    clearDraft("k");
    expect(loadDraft("k")).toBeNull();
  });

  describe("formatDraftAge", () => {
    it("returns 'just now' for a fresh timestamp", () => {
      expect(formatDraftAge(Date.now())).toBe("just now");
    });

    it("formats minutes under an hour", () => {
      expect(formatDraftAge(Date.now() - 5 * 60_000)).toBe("5m ago");
    });

    it("formats hours under a day", () => {
      expect(formatDraftAge(Date.now() - 3 * 60 * 60_000)).toBe("3h ago");
    });

    it("formats days after that", () => {
      expect(formatDraftAge(Date.now() - 2 * 24 * 60 * 60_000)).toBe("2d ago");
    });
  });

  describe("createDebouncedSaver", () => {
    it("only writes after the delay", () => {
      const saver = createDebouncedSaver<string>("k", 300);
      saver.schedule("one");
      saver.schedule("two");
      saver.schedule("three");
      expect(loadDraft<string>("k")).toBeNull();
      vi.advanceTimersByTime(299);
      expect(loadDraft<string>("k")).toBeNull();
      vi.advanceTimersByTime(1);
      expect(loadDraft<string>("k")).toBe("three");
    });

    it("flush forces an immediate write", () => {
      const saver = createDebouncedSaver<string>("k", 10_000);
      saver.schedule("x");
      expect(loadDraft<string>("k")).toBeNull();
      saver.flush();
      expect(loadDraft<string>("k")).toBe("x");
    });

    it("cancel drops a pending write", () => {
      const saver = createDebouncedSaver<string>("k", 300);
      saver.schedule("x");
      saver.cancel();
      vi.advanceTimersByTime(500);
      expect(loadDraft<string>("k")).toBeNull();
    });
  });
});
