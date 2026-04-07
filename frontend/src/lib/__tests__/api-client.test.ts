import { describe, it, expect, vi, beforeEach } from "vitest";

// Polyfill localStorage for Node.js test environment
const store: Record<string, string> = {};
const localStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, value: string) => { store[key] = value; },
  removeItem: (key: string) => { delete store[key]; },
  clear: () => { for (const k in store) delete store[k]; },
  get length() { return Object.keys(store).length; },
  key: (i: number) => Object.keys(store)[i] ?? null,
};
Object.defineProperty(globalThis, "localStorage", { value: localStorageMock, writable: true });

vi.mock("aws-amplify/auth", () => ({
  fetchAuthSession: vi.fn(),
}));

import { fetchAuthSession } from "aws-amplify/auth";
import { apiGet, apiPost, apiPut, apiDelete, getWorkspaceId, setWorkspaceId } from "../api-client";

const mockFetchAuthSession = vi.mocked(fetchAuthSession);

beforeEach(() => {
  vi.restoreAllMocks();
  localStorageMock.clear();
  mockFetchAuthSession.mockResolvedValue({
    tokens: { idToken: { toString: () => "mock-id-token" } },
  } as any);
  global.fetch = vi.fn();
});

describe("api-client", () => {
  describe("workspace ID 管理", () => {
    it("setWorkspaceId 保存到 localStorage", () => {
      setWorkspaceId("ws-123");
      expect(getWorkspaceId()).toBe("ws-123");
    });

    it("getWorkspaceId 无值时返回 default", () => {
      localStorage.removeItem("agent-studio-workspace-id");
      expect(getWorkspaceId()).toBe("default");
    });
  });

  describe("apiGet", () => {
    it("发送带 Authorization header 的 GET 请求", async () => {
      const mockResponse = { ok: true, json: () => Promise.resolve({ items: [] }) };
      (global.fetch as any).mockResolvedValue(mockResponse);

      setWorkspaceId("ws-abc");
      const result = await apiGet("/agents");

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents",
        expect.objectContaining({
          method: "GET",
          headers: expect.objectContaining({
            Authorization: "Bearer mock-id-token",
          }),
        })
      );
      expect(result).toEqual({ items: [] });
    });

    it("未认证时抛出错误", async () => {
      mockFetchAuthSession.mockResolvedValue({ tokens: undefined } as any);
      await expect(apiGet("/agents")).rejects.toThrow("Not authenticated");
    });

    it("HTTP 错误时抛出带 status 的错误", async () => {
      (global.fetch as any).mockResolvedValue({
        ok: false, status: 403,
        json: () => Promise.resolve({ error: "Forbidden" }),
      });
      setWorkspaceId("ws-abc");
      await expect(apiGet("/agents")).rejects.toThrow("403");
    });
  });

  describe("apiPost", () => {
    it("发送 JSON body 的 POST 请求", async () => {
      (global.fetch as any).mockResolvedValue({
        ok: true, json: () => Promise.resolve({ agentId: "a-1" }),
      });
      setWorkspaceId("ws-abc");
      const result = await apiPost("/agents", { name: "test" });

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "Content-Type": "application/json",
          }),
          body: JSON.stringify({ name: "test" }),
        })
      );
      expect(result).toEqual({ agentId: "a-1" });
    });
  });

  describe("apiPut", () => {
    it("发送 PUT 请求", async () => {
      (global.fetch as any).mockResolvedValue({
        ok: true, json: () => Promise.resolve({ updated: true }),
      });
      setWorkspaceId("ws-abc");
      await apiPut("/agents/a-1", { name: "updated" });

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents/a-1",
        expect.objectContaining({ method: "PUT" })
      );
    });
  });

  describe("apiDelete", () => {
    it("发送 DELETE 请求", async () => {
      (global.fetch as any).mockResolvedValue({
        ok: true, json: () => Promise.resolve({ deleted: true }),
      });
      setWorkspaceId("ws-abc");
      await apiDelete("/agents/a-1");

      expect(global.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents/a-1",
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });
});
