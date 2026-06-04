import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const listAgentEvaluations = vi.fn();
vi.mock("../../lib/api-client", () => ({
  listAgentEvaluations: (...args: unknown[]) => listAgentEvaluations(...args),
}));

import { useAgentEvaluations } from "../useAgentEvaluations";

// Note: mockReset() resets mockImplementation to synchronous default,
// which causes the error-handling test to see a rejected Promise with
// no catch handler attached before its then — we clear per-test manually.
beforeEach(() => {
  listAgentEvaluations.mockClear();
});

// API contract: listAgentEvaluations returns
//   { evaluations: AgentEvaluation[], diagnostics?: EvaluationDiagnostics }
// not a bare array. Tests must wrap their data accordingly.
describe("useAgentEvaluations", () => {
  it("groups scores by evaluator + computes mean", async () => {
    listAgentEvaluations.mockResolvedValue({
      evaluations: [
        { timestamp: "t1", evaluator: "Builtin.Correctness", score: 0.8, sessionId: "s1" },
        { timestamp: "t2", evaluator: "Builtin.Correctness", score: 1.0, sessionId: "s2" },
        { timestamp: "t3", evaluator: "Builtin.Helpfulness", score: 0.6, sessionId: "s1" },
      ],
    });
    const { result } = renderHook(() => useAgentEvaluations("agt-1"));
    await waitFor(() => expect(result.current.grouped).toBeTruthy());

    const g = result.current.grouped!;
    expect(g["Builtin.Correctness"].mean).toBeCloseTo(0.9);
    expect(g["Builtin.Correctness"].latest).toBe(0.8);
    expect(g["Builtin.Correctness"].count).toBe(2);
    expect(g["Builtin.Helpfulness"].count).toBe(1);
  });

  it("surfaces empty when no rows", async () => {
    listAgentEvaluations.mockResolvedValue({ evaluations: [] });
    const { result } = renderHook(() => useAgentEvaluations("agt-2"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.grouped).toEqual({});
  });

  it("handles errors", async () => {
    listAgentEvaluations.mockImplementation(() => Promise.reject(new Error("boom")));
    const { result } = renderHook(() => useAgentEvaluations("agt-err"));
    await waitFor(() => expect(result.current.error?.message).toBe("boom"));
  });
});
