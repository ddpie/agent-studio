import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const listTraces = vi.fn();
const getSessionTrace = vi.fn();
vi.mock("../../lib/api-client", () => ({
  listTraces: (...a: unknown[]) => listTraces(...a),
  getSessionTrace: (...a: unknown[]) => getSessionTrace(...a),
}));

import { useTraceSessions, useSessionTrace } from "../useTraces";

beforeEach(() => {
  listTraces.mockClear();
  getSessionTrace.mockClear();
});

describe("useTraceSessions", () => {
  it("fetches sessions for agent", async () => {
    listTraces.mockResolvedValue([
      { sessionId: "s1", spanCount: 12 },
      { sessionId: "s2", spanCount: 4 },
    ]);
    const { result } = renderHook(() => useTraceSessions("agt-1"));
    await waitFor(() => expect(result.current.sessions?.length).toBe(2));
    expect(listTraces).toHaveBeenCalledWith("agt-1");
  });
});

describe("useSessionTrace", () => {
  it("fetches when sessionId set, not when null", async () => {
    getSessionTrace.mockResolvedValue({
      spanId: "r",
      parentSpanId: null,
      name: "root",
      startMs: 0,
      durationMs: 100,
      status: "OK",
      children: [],
    });
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string | null }) => useSessionTrace("agt-1", sid),
      { initialProps: { sid: null as string | null } }
    );
    expect(getSessionTrace).not.toHaveBeenCalled();
    rerender({ sid: "s-abc" });
    await waitFor(() => expect(result.current.root?.spanId).toBe("r"));
    expect(getSessionTrace).toHaveBeenCalledWith("agt-1", "s-abc");
  });
});
