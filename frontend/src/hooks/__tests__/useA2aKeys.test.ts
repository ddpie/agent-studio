import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

const listA2aKeys = vi.fn();
const createA2aKey = vi.fn();
const revokeA2aKey = vi.fn();
const listMetaA2aKeys = vi.fn();
const createMetaA2aKey = vi.fn();
const revokeMetaA2aKey = vi.fn();

vi.mock("../../lib/api-client", () => ({
  listA2aKeys: (...a: unknown[]) => listA2aKeys(...a),
  createA2aKey: (...a: unknown[]) => createA2aKey(...a),
  revokeA2aKey: (...a: unknown[]) => revokeA2aKey(...a),
  listMetaA2aKeys: (...a: unknown[]) => listMetaA2aKeys(...a),
  createMetaA2aKey: (...a: unknown[]) => createMetaA2aKey(...a),
  revokeMetaA2aKey: (...a: unknown[]) => revokeMetaA2aKey(...a),
}));

import { useA2aKeys } from "../useA2aKeys";

beforeEach(() => {
  for (const fn of [listA2aKeys, createA2aKey, revokeA2aKey, listMetaA2aKeys, createMetaA2aKey, revokeMetaA2aKey]) {
    fn.mockClear();
  }
});

describe("useA2aKeys (agent)", () => {
  it("loads keys on mount", async () => {
    listA2aKeys.mockResolvedValue([{ keyId: "k1", keyPrefix: "as_abcd", createdAt: "t", revoked: false }]);
    const { result } = renderHook(() => useA2aKeys("agt-1"));
    await waitFor(() => expect(result.current.keys?.length).toBe(1));
    expect(listA2aKeys).toHaveBeenCalledWith("agt-1");
  });

  it("generate returns plaintext once and refreshes list", async () => {
    listA2aKeys.mockResolvedValue([]);
    createA2aKey.mockResolvedValue({ keyId: "k1", keyPrefix: "as_abcd", apiKey: "as_secret", createdAt: "t", revoked: false });
    const { result } = renderHook(() => useA2aKeys("agt-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let created: unknown;
    await act(async () => {
      created = await result.current.generate();
    });
    expect((created as { apiKey: string }).apiKey).toBe("as_secret");
    expect(createA2aKey).toHaveBeenCalledWith("agt-1");
  });

  it("revoke calls the right api and refreshes", async () => {
    listA2aKeys
      .mockResolvedValueOnce([{ keyId: "k1", keyPrefix: "as_abcd", createdAt: "t", revoked: false }])
      .mockResolvedValueOnce([{ keyId: "k1", keyPrefix: "as_abcd", createdAt: "t", revoked: true }]);
    revokeA2aKey.mockResolvedValue(undefined);

    const { result } = renderHook(() => useA2aKeys("agt-1"));
    await waitFor(() => expect(result.current.keys?.length).toBe(1));

    await act(async () => {
      await result.current.revoke("k1");
    });
    expect(revokeA2aKey).toHaveBeenCalledWith("agt-1", "k1");
  });
});

describe("useA2aKeys (meta-agent)", () => {
  it("uses meta endpoints when kind=meta-agent", async () => {
    listMetaA2aKeys.mockResolvedValue([{ keyId: "m1", keyPrefix: "as_mmmm", createdAt: "t", revoked: false }]);
    const { result } = renderHook(() => useA2aKeys("meta-agent", "meta-agent"));
    await waitFor(() => expect(result.current.keys?.length).toBe(1));
    expect(listMetaA2aKeys).toHaveBeenCalled();
    expect(listA2aKeys).not.toHaveBeenCalled();
  });
});
