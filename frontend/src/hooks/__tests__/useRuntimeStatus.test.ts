import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const getAgentRuntime = vi.fn();
vi.mock("../../lib/api-client", () => ({
  getAgentRuntime: (...args: unknown[]) => getAgentRuntime(...args),
}));

import { useRuntimeStatus } from "../useRuntimeStatus";

beforeEach(() => {
  getAgentRuntime.mockReset();
});

describe("useRuntimeStatus", () => {
  it("returns status after fetch", async () => {
    getAgentRuntime.mockResolvedValue({ status: "ACTIVE", agentRuntimeVersion: "3" });
    const { result } = renderHook(() => useRuntimeStatus("agt-1"));
    await waitFor(() => expect(result.current.data?.status).toBe("ACTIVE"));
    expect(getAgentRuntime).toHaveBeenCalledWith("agt-1");
  });

  it("caches for 30s — no second fetch for same id", async () => {
    getAgentRuntime.mockResolvedValue({ status: "ACTIVE" });
    const { result, rerender } = renderHook((id: string) => useRuntimeStatus(id), {
      initialProps: "agt-2",
    });
    await waitFor(() => expect(result.current.data).toBeTruthy());
    rerender("agt-2");
    await waitFor(() => expect(result.current.data).toBeTruthy());
    expect(getAgentRuntime).toHaveBeenCalledTimes(1);
  });

  it("surfaces errors", async () => {
    getAgentRuntime.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useRuntimeStatus("agt-err"));
    await waitFor(() => expect(result.current.error?.message).toBe("boom"));
  });
});
