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

vi.mock("../../config", () => ({
  agentConfig: { apiUrl: "" },
}));

// Avoid loading the real chat-store / agent-edit-store / draft-autosave when
// clearUserScopedLocalData / setWorkspaceId trigger dynamic imports — they
// pull in Zustand and react-router which aren't available in this isolated
// test. Stubbing keeps the assertions focused on the api-client code paths.
vi.mock("../../stores/chat-store", () => ({
  resetChatForWorkspaceSwitch: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("../../stores/agent-edit-store", () => ({
  cancelAllAgentDraftSavers: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("../draft-autosave", async () => {
  const actual = await vi.importActual<typeof import("../draft-autosave")>("../draft-autosave");
  return {
    ...actual,
    clearDraftsByPrefix: vi.fn(),
  };
});

import { fetchAuthSession } from "aws-amplify/auth";
import * as api from "../api-client";

const mockFetchAuthSession = vi.mocked(fetchAuthSession);

function mockJson(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorageMock.clear();
  mockFetchAuthSession.mockResolvedValue({
    tokens: {
      idToken: {
        toString: () => "mock-id-token",
        payload: { sub: "user-1", "cognito:groups": ["platform-admins"] },
      },
    },
  } as any);
  globalThis.fetch = vi.fn() as any;
  api.setWorkspaceId("ws-abc");
});

describe("api-client core", () => {
  describe("workspace ID 管理", () => {
    it("setWorkspaceId 保存到 localStorage", () => {
      api.setWorkspaceId("ws-123");
      expect(api.getWorkspaceId()).toBe("ws-123");
    });

    it("getWorkspaceId 无值时返回 default", () => {
      localStorage.removeItem("agent-studio-workspace-id");
      expect(api.getWorkspaceId()).toBe("default");
    });

    it("setWorkspaceId 切换 workspace 时返回 promise 并触发清理", async () => {
      api.setWorkspaceId("ws-old");
      const result = api.setWorkspaceId("ws-new");
      // Promise (clears chat store + cancels savers)
      await expect(result).resolves.toBeUndefined();
      expect(api.getWorkspaceId()).toBe("ws-new");
    });
  });

  describe("apiGet/apiPost/apiPut/apiDelete shared paths", () => {
    it("apiGet sends Authorization header and returns parsed body", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
      const result = await api.apiGet("/agents");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents",
        expect.objectContaining({
          method: "GET",
          headers: expect.objectContaining({ Authorization: "Bearer mock-id-token" }),
        }),
      );
      expect(result).toEqual({ items: [] });
    });

    it("apiPost serialises body and sets Content-Type", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ ok: true }));
      await api.apiPost("/agents", { name: "x" });
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/workspaces/ws-abc/agents",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ name: "x" }),
          headers: expect.objectContaining({ "Content-Type": "application/json" }),
        }),
      );
    });

    it("apiPut & apiDelete dispatch correct methods", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.apiPut("/x", { a: 1 });
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("PUT");
      await api.apiDelete("/x");
      expect((globalThis.fetch as any).mock.calls[1][1].method).toBe("DELETE");
    });

    it("未认证时抛出 Not authenticated", async () => {
      mockFetchAuthSession.mockResolvedValue({ tokens: undefined } as any);
      await expect(api.apiGet("/x")).rejects.toThrow("Not authenticated");
    });

    it("HTTP 4xx 抛出 ApiError 带 status", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false,
        status: 403,
        json: () => Promise.resolve({ error: "Forbidden" }),
      });
      await expect(api.apiGet("/x")).rejects.toMatchObject({ status: 403 });
    });

    it("HTTP 5xx 抛出 ApiError 带 status", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ error: "Boom" }),
      });
      await expect(api.apiGet("/x")).rejects.toMatchObject({ status: 500 });
    });

    it("响应 4xx 但 body 不是 JSON 时不爆炸", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false,
        status: 400,
        json: () => Promise.reject(new Error("not json")),
      });
      await expect(api.apiGet("/x")).rejects.toMatchObject({ status: 400 });
    });

    it("204 NoContent 返回 undefined", async () => {
      (globalThis.fetch as any).mockResolvedValue({ ok: true, status: 204 });
      const result = await api.apiDelete("/x");
      expect(result).toBeUndefined();
    });

    it("网络错误抛出 ApiError(0)", async () => {
      (globalThis.fetch as any).mockRejectedValue(new TypeError("network"));
      await expect(api.apiGet("/x")).rejects.toMatchObject({ status: 0 });
    });

    it("401 触发刷新 token + 重试", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce({ ok: false, status: 401, json: () => Promise.resolve({}) });
      fetchMock.mockResolvedValueOnce(mockJson({ ok: true }));
      const result = await api.apiGet("/x");
      expect(result).toEqual({ ok: true });
      expect(fetchMock).toHaveBeenCalledTimes(2);
      // forceRefresh used on retry
      expect(mockFetchAuthSession).toHaveBeenCalledWith({ forceRefresh: true });
    });

    it("401 后第二次请求网络错误 → ApiError(0)", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce({ ok: false, status: 401, json: () => Promise.resolve({}) });
      fetchMock.mockRejectedValueOnce(new TypeError("net"));
      await expect(api.apiGet("/x")).rejects.toMatchObject({ status: 0 });
    });

    it("apiGetRaw/apiPostRaw/apiPutRaw/apiDeleteRaw 使用绝对路径并支持 extraHeaders", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValue(mockJson({ ok: true }));
      await api.apiGetRaw("/api/public/x");
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/public/x",
        expect.objectContaining({ method: "GET" }),
      );
      await api.apiPostRaw("/api/x", { a: 1 }, { "x-extra": "1" });
      const lastCall = fetchMock.mock.calls.at(-1)!;
      expect(lastCall[1].headers["x-extra"]).toBe("1");
      await api.apiPutRaw("/api/x", { a: 1 });
      await api.apiDeleteRaw("/api/x");
      expect(fetchMock).toHaveBeenCalledTimes(4);
    });

    it("apiGetRaw 拒绝非 /api 路径", async () => {
      await expect(api.apiGetRaw("/agents")).rejects.toThrow(/only supports/);
    });
  });

  describe("identity & auth helpers", () => {
    it("isPlatformAdmin true when group membership matches", async () => {
      await expect(api.isPlatformAdmin()).resolves.toBe(true);
    });

    it("isPlatformAdmin false when groups absent", async () => {
      mockFetchAuthSession.mockResolvedValue({
        tokens: { idToken: { toString: () => "t", payload: { sub: "u" } } },
      } as any);
      await expect(api.isPlatformAdmin()).resolves.toBe(false);
    });

    it("isPlatformAdmin false on session error", async () => {
      mockFetchAuthSession.mockRejectedValue(new Error("nope"));
      await expect(api.isPlatformAdmin()).resolves.toBe(false);
    });

    it("enforceUserIdentity stores sub on first login", async () => {
      await api.enforceUserIdentity();
      expect(localStorage.getItem("agent-studio-last-user-sub")).toBe("user-1");
    });

    it("enforceUserIdentity wipes when sub differs from stored", async () => {
      localStorage.setItem("agent-studio-last-user-sub", "user-old");
      await api.enforceUserIdentity();
      expect(localStorage.getItem("agent-studio-last-user-sub")).toBe("user-1");
    });

    it("enforceUserIdentity early-returns when same sub", async () => {
      localStorage.setItem("agent-studio-last-user-sub", "user-1");
      await api.enforceUserIdentity();
      expect(localStorage.getItem("agent-studio-last-user-sub")).toBe("user-1");
    });

    it("enforceUserIdentity bails on session error", async () => {
      mockFetchAuthSession.mockRejectedValue(new Error("nope"));
      await expect(api.enforceUserIdentity()).resolves.toBeUndefined();
    });

    it("enforceUserIdentity ignores when no sub in token", async () => {
      mockFetchAuthSession.mockResolvedValue({
        tokens: { idToken: { toString: () => "t", payload: {} } },
      } as any);
      await expect(api.enforceUserIdentity()).resolves.toBeUndefined();
    });

    it("clearUserScopedLocalData wipes WS_KEY and USER_KEY", async () => {
      localStorage.setItem("agent-studio-last-user-sub", "u");
      await api.clearUserScopedLocalData();
      expect(localStorage.getItem("agent-studio-workspace-id")).toBeNull();
      expect(localStorage.getItem("agent-studio-last-user-sub")).toBeNull();
    });

    it("ensureWorkspaceId returns existing if already set", async () => {
      localStorage.setItem("agent-studio-workspace-id", "ws-existing");
      const id = await api.ensureWorkspaceId();
      expect(id).toBe("ws-existing");
      expect(globalThis.fetch).not.toHaveBeenCalled();
    });

    it("ensureWorkspaceId fetches the user's workspace list when default", async () => {
      localStorage.setItem("agent-studio-workspace-id", "default");
      (globalThis.fetch as any).mockResolvedValue(
        mockJson({ items: [{ workspaceId: "ws-real" }] }),
      );
      const id = await api.ensureWorkspaceId();
      expect(id).toBe("ws-real");
      expect(api.getWorkspaceId()).toBe("ws-real");
    });

    it("ensureWorkspaceId tolerates fetch failure", async () => {
      localStorage.setItem("agent-studio-workspace-id", "default");
      (globalThis.fetch as any).mockRejectedValue(new Error("boom"));
      const id = await api.ensureWorkspaceId();
      expect(id).toBe("default");
    });

    it("ensureWorkspaceId returns default when items empty", async () => {
      localStorage.removeItem("agent-studio-workspace-id");
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
      const id = await api.ensureWorkspaceId();
      expect(id).toBe("default");
    });
  });

  describe("Agent CRUD", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ ok: true, items: [], agentId: "a-1" }));
    });

    it("fetchAgents builds query string", async () => {
      await api.fetchAgents("cur-1", 25);
      const url = (globalThis.fetch as any).mock.calls[0][0];
      expect(url).toContain("/agents?");
      expect(url).toContain("limit=25");
      expect(url).toContain("cursor=cur-1");
    });

    it("fetchAgents without cursor", async () => {
      await api.fetchAgents();
      expect((globalThis.fetch as any).mock.calls[0][0]).toContain("limit=50");
    });

    it("fetchAgent encodes id", async () => {
      await api.fetchAgent("agent/with slash");
      expect((globalThis.fetch as any).mock.calls[0][0]).toContain(
        encodeURIComponent("agent/with slash"),
      );
    });

    it("createAgent posts to /agents", async () => {
      await api.createAgent({ name: "foo" });
      const [url, init] = (globalThis.fetch as any).mock.calls[0];
      expect(url).toMatch(/\/agents$/);
      expect(init.method).toBe("POST");
    });

    it("updateAgent puts to /agents/{id}", async () => {
      await api.updateAgent("a1", { name: "x" });
      const [url, init] = (globalThis.fetch as any).mock.calls[0];
      expect(url).toMatch(/\/agents\/a1$/);
      expect(init.method).toBe("PUT");
    });

    it("deleteAgent deletes /agents/{id}", async () => {
      await api.deleteAgent("a1");
      const [, init] = (globalThis.fetch as any).mock.calls[0];
      expect(init.method).toBe("DELETE");
    });

    it("fetchAgentFile returns content", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ content: "hello" }));
      await expect(api.fetchAgentFile("a1", "main.py")).resolves.toBe("hello");
    });

    it("fetchAgentFile returns '' on error", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false, status: 404, json: () => Promise.resolve({}),
      });
      await expect(api.fetchAgentFile("a1", "main.py")).resolves.toBe("");
    });

    it("putAgentFile invokes PUT", async () => {
      await api.putAgentFile("a1", "main.py", "code");
      const [, init] = (globalThis.fetch as any).mock.calls[0];
      expect(init.method).toBe("PUT");
    });
  });

  describe("A2A keys", () => {
    it("listA2aKeys returns []", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await expect(api.listA2aKeys("a1")).resolves.toEqual([]);
    });

    it("listA2aKeys returns keys[]", async () => {
      const k = { keyId: "k1", keyPrefix: "ag_", createdAt: "t", revoked: false };
      (globalThis.fetch as any).mockResolvedValue(mockJson({ keys: [k] }));
      await expect(api.listA2aKeys("a1")).resolves.toEqual([k]);
    });

    it("createA2aKey + revokeA2aKey", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ keyId: "k", apiKey: "x" }));
      await api.createA2aKey("a1");
      await api.revokeA2aKey("a1", "k");
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("POST");
      expect((globalThis.fetch as any).mock.calls[1][1].method).toBe("DELETE");
    });

    it("listMetaA2aKeys / createMetaA2aKey / revokeMetaA2aKey", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.listMetaA2aKeys();
      await api.createMetaA2aKey();
      await api.revokeMetaA2aKey("k");
      expect((globalThis.fetch as any).mock.calls.map((c: any) => c[1].method)).toEqual([
        "GET",
        "POST",
        "DELETE",
      ]);
    });

    it("getPublicAgentCardUrl/getA2aEndpointUrl/getMetaA2a* build URLs", () => {
      expect(api.getPublicAgentCardUrl("a1")).toContain("/a2a/agents/a1/.well-known");
      expect(api.getA2aEndpointUrl("a1")).toContain("/a2a/agents/a1");
      expect(api.getMetaA2aCardUrl()).toContain("/.well-known/agent-card.json");
      expect(api.getMetaA2aEndpointUrl()).toMatch(/\/a2a\/meta-agent$/);
    });
  });

  describe("Schedules", () => {
    it("listAgentSchedules returns [] on missing field", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await expect(api.listAgentSchedules("a1")).resolves.toEqual([]);
    });

    it("createAgentSchedule POST", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ name: "n" }));
      await api.createAgentSchedule("a1", { name: "n", cron: "rate(1 minute)", prompt: "p" });
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("POST");
    });

    it("updateAgentSchedule PUT", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.updateAgentSchedule("a1", "n", { state: "ENABLED" });
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("PUT");
    });

    it("deleteAgentSchedule + runAgentScheduleNow + listScheduleExecutions", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ executions: [] }));
      await api.deleteAgentSchedule("a1", "n");
      await api.runAgentScheduleNow("a1", "n");
      await api.listScheduleExecutions("a1", "n");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["DELETE", "POST", "GET"]);
    });

    it("listScheduleExecutions returns executions", async () => {
      const exec = { sessionId: "s", scheduledTime: "t", durationMs: 10, status: "success" };
      (globalThis.fetch as any).mockResolvedValue(mockJson({ executions: [exec] }));
      await expect(api.listScheduleExecutions("a", "n")).resolves.toEqual([exec]);
    });
  });

  describe("Agent secrets", () => {
    it("listAgentSecrets returns items", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [{ key: "API_KEY" }] }));
      await expect(api.listAgentSecrets("a1")).resolves.toEqual([{ key: "API_KEY" }]);
    });

    it("listAgentSecrets returns [] on missing", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await expect(api.listAgentSecrets("a1")).resolves.toEqual([]);
    });

    it("putAgentSecret + deleteAgentSecret", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.putAgentSecret("a1", "K", "V");
      await api.deleteAgentSecret("a1", "K");
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("POST");
      expect((globalThis.fetch as any).mock.calls[1][1].method).toBe("DELETE");
    });
  });

  describe("Agent runtime / versions / endpoints", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
    });

    it("getAgentRuntime", async () => {
      await api.getAgentRuntime("a1");
      expect((globalThis.fetch as any).mock.calls[0][0]).toContain("/runtime");
    });

    it("listAgentVersions returns []", async () => {
      await expect(api.listAgentVersions("a1")).resolves.toEqual([]);
    });

    it("listAgentEndpoints returns []", async () => {
      await expect(api.listAgentEndpoints("a1")).resolves.toEqual([]);
    });

    it("createAgentEndpoint / updateAgentEndpoint / deleteAgentEndpoint", async () => {
      await api.createAgentEndpoint("a1", { name: "live", version: "v1" });
      await api.updateAgentEndpoint("a1", "live", "v2");
      await api.deleteAgentEndpoint("a1", "live");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["POST", "PUT", "DELETE"]);
    });
  });

  describe("Agent evaluations", () => {
    it("listAgentEvaluations returns evaluations + diagnostics", async () => {
      (globalThis.fetch as any).mockResolvedValue(
        mockJson({ evaluations: [{ score: 1 }], diagnostics: { allFailed: false } }),
      );
      const r = await api.listAgentEvaluations("a1");
      expect(r.evaluations).toHaveLength(1);
      expect(r.diagnostics).toBeDefined();
    });

    it("listAgentEvaluations defaults to empty array", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      const r = await api.listAgentEvaluations("a1");
      expect(r.evaluations).toEqual([]);
    });

    it("getAgentEvaluationStatus + enableAgentEvaluations", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.getAgentEvaluationStatus("a1");
      await api.enableAgentEvaluations("a1");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["GET", "POST"]);
    });
  });

  describe("Logs", () => {
    it("fetchAgentLogs sets defaults", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ events: [] }));
      await api.fetchAgentLogs("a1");
      const url = (globalThis.fetch as any).mock.calls[0][0];
      expect(url).toContain("since=1h");
      expect(url).toContain("level=ALL");
    });

    it("fetchAgentLogs forwards optional params", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.fetchAgentLogs("a1", { since: "6h", level: "ERROR", search: "boom", cursor: "c1" });
      const url = (globalThis.fetch as any).mock.calls[0][0];
      expect(url).toContain("since=6h");
      expect(url).toContain("level=ERROR");
      expect(url).toContain("search=boom");
      expect(url).toContain("cursor=c1");
    });
  });

  describe("Meta-agent card / status / Kiro key", () => {
    it("getMetaAgentCard returns card", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ name: "meta" }));
      await expect(api.getMetaAgentCard()).resolves.toEqual({ name: "meta" });
    });

    it("getMetaAgentCard returns null on error", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.getMetaAgentCard()).resolves.toBeNull();
    });

    it("getMetaAgentStatus returns status / null on error", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ status: "READY", lastUpdated: null }));
      await expect(api.getMetaAgentStatus()).resolves.toEqual({ status: "READY", lastUpdated: null });
      (globalThis.fetch as any).mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.getMetaAgentStatus()).resolves.toBeNull();
    });

    it("getKiroKey / putKiroKey / deleteKiroKey / getKiroUsage", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ configured: true }));
      await api.getKiroKey();
      await api.putKiroKey("ak", "us-east-1");
      await api.deleteKiroKey();
      await api.getKiroUsage();
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["GET", "PUT", "DELETE", "GET"]);
    });
  });

  describe("Uploads / downloads", () => {
    it("getImageUploadUrl + getAttachmentUploadUrl", async () => {
      (globalThis.fetch as any).mockResolvedValue(
        mockJson({ uploadUrl: "https://s3", fields: {}, s3Key: "k" }),
      );
      await api.getImageUploadUrl("f.png", "image/png");
      await api.getAttachmentUploadUrl("f.txt", "text/plain", "sess-1");
      expect((globalThis.fetch as any).mock.calls).toHaveLength(2);
    });

    it("uploadWithPresignedPost issues multipart POST", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValue({ ok: true });
      const blob = new Blob(["abc"], { type: "text/plain" });
      await api.uploadWithPresignedPost(
        { url: "https://s3.example.com", fields: { key: "abc" } },
        blob,
      );
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe("https://s3.example.com");
      expect(init.method).toBe("POST");
    });

    it("uploadWithPresignedPost throws on non-2xx", async () => {
      (globalThis.fetch as any).mockResolvedValue({ ok: false, status: 500 });
      await expect(
        api.uploadWithPresignedPost({ url: "u", fields: {} }, new Blob([])),
      ).rejects.toThrow(/Upload failed/);
    });

    it("getDownloadUrl returns url", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ url: "https://signed" }));
      await expect(api.getDownloadUrl("k")).resolves.toBe("https://signed");
    });

    it("getDownloadUrl returns null on error", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.getDownloadUrl("k")).resolves.toBeNull();
    });
  });

  describe("Tools API + cache", () => {
    it("fetchTools caches first page for 5 min", async () => {
      api.invalidateToolsCache();
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [{ toolId: "t" }] }));
      const r1 = await api.fetchTools();
      const r2 = await api.fetchTools(); // cached
      expect((globalThis.fetch as any)).toHaveBeenCalledTimes(1);
      expect(r1.items).toEqual(r2.items);
    });

    it("fetchTools paginates via cursor (no cache)", async () => {
      api.invalidateToolsCache();
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
      await api.fetchTools("cursor-1");
      const url = (globalThis.fetch as any).mock.calls[0][0];
      expect(url).toContain("cursor=cursor-1");
    });

    it("invalidateToolsCache forces refetch", async () => {
      api.invalidateToolsCache();
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
      await api.fetchTools();
      api.invalidateToolsCache();
      await api.fetchTools();
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });

    it("createOrUpdateTool POST when no toolId, PUT with toolId", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ toolId: "t" }));
      await api.createOrUpdateTool({ name: "x" });
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("POST");
      await api.createOrUpdateTool({ toolId: "t1", name: "x" });
      expect((globalThis.fetch as any).mock.calls[1][1].method).toBe("PUT");
    });

    it("createOrUpdateTool propagates errors", async () => {
      (globalThis.fetch as any).mockResolvedValue({
        ok: false, status: 400, json: () => Promise.resolve({ error: "bad" }),
      });
      await expect(api.createOrUpdateTool({ name: "x" })).rejects.toMatchObject({ status: 400 });
    });

    it("deleteToolApi returns true on success and false on error", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteToolApi("t")).resolves.toBe(true);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.deleteToolApi("t")).resolves.toBe(false);
    });

    it("restoreToolApi/permanentDeleteToolApi success + failure", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.restoreToolApi("t")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.restoreToolApi("t")).resolves.toBe(false);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.permanentDeleteToolApi("t")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.permanentDeleteToolApi("t")).resolves.toBe(false);
    });

    it("fetchDeletedTools returns array and [] on error", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson([{ toolId: "t" }]));
      await expect(api.fetchDeletedTools()).resolves.toEqual([{ toolId: "t" }]);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.fetchDeletedTools()).resolves.toEqual([]);
    });
  });

  describe("Public marketplace + clone", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
    });

    it("fetchPublic{Agents,Skills,Tools}", async () => {
      await api.fetchPublicAgents();
      await api.fetchPublicSkills("c");
      await api.fetchPublicTools(undefined, 5);
      expect(globalThis.fetch).toHaveBeenCalledTimes(3);
    });

    it("clonePublicAgent passes name + workspace header", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ agentId: "x" }));
      await api.clonePublicAgent("src", "Friendly");
      const init = (globalThis.fetch as any).mock.calls[0][1];
      expect(init.headers["x-workspace-id"]).toBe("ws-abc");
      expect(JSON.parse(init.body)).toEqual({ name: "Friendly" });
    });

    it("clonePublicAgent without name sends {}", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ agentId: "x" }));
      await api.clonePublicAgent("src");
      const init = (globalThis.fetch as any).mock.calls[0][1];
      expect(JSON.parse(init.body)).toEqual({});
    });

    it("clonePublicSkill / clonePublicTool", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.clonePublicSkill("s");
      await api.clonePublicTool("t");
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });
  });

  describe("Publish / unpublish / approve", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
    });

    it("publishAgent / unpublishAgent / publishSkill / unpublishSkill / approveSkill / publishTool / unpublishTool", async () => {
      await api.publishAgent("a");
      await api.unpublishAgent("a");
      await api.publishSkill("s");
      await api.unpublishSkill("s");
      await api.approveSkill("s");
      await api.publishTool("t");
      await api.unpublishTool("t");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods.every((m: string) => m === "POST")).toBe(true);
    });
  });

  describe("Skills file APIs", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
    });

    it("fetchSkills + fetchDeletedSkills", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [], nextCursor: "n" }));
      await api.fetchSkills("c");
      await api.fetchDeletedSkills();
      const calls = (globalThis.fetch as any).mock.calls;
      expect(calls[0][0]).toContain("/skills?");
      expect(calls[1][0]).toContain("deleted=true");
    });

    it("fetchSkillFiles success + failure", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({ files: ["a", "b"] }));
      await expect(api.fetchSkillFiles("s")).resolves.toEqual(["a", "b"]);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.fetchSkillFiles("s")).resolves.toEqual([]);
    });

    it("fetchSkillFile success / failure", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({ content: "c" }));
      await expect(api.fetchSkillFile("s", "p")).resolves.toBe("c");
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.fetchSkillFile("s", "p")).resolves.toBeNull();
    });

    it("putSkillFile / deleteSkillFileApi success+failure", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({}));
      await expect(api.putSkillFile("s", "p", "x")).resolves.toBe(true);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.putSkillFile("s", "p", "x")).resolves.toBe(false);
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteSkillFileApi("s", "p")).resolves.toBe(true);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.deleteSkillFileApi("s", "p")).resolves.toBe(false);
    });

    it("importSkillApi success / failure", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({ skillId: "s" }));
      await expect(api.importSkillApi({ content: "x" })).resolves.toEqual({ skillId: "s" });
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.importSkillApi({ content: "x" })).resolves.toBeNull();
    });

    it("restoreSkillApi / permanentlyDeleteSkillApi / deleteSkillApi", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.restoreSkillApi("s")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.restoreSkillApi("s")).resolves.toBe(false);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.permanentlyDeleteSkillApi("s")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.permanentlyDeleteSkillApi("s")).resolves.toBe(false);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteSkillApi("s")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.deleteSkillApi("s")).resolves.toBe(false);
    });
  });

  describe("Workspace + invitations", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ items: [] }));
    });

    it("fetchWorkspaces / fetchAllWorkspacesAsAdmin / createWorkspace / fetchWorkspaceDetail", async () => {
      await api.fetchWorkspaces();
      await api.fetchAllWorkspacesAsAdmin();
      await api.fetchAllWorkspacesAsAdmin("nx");
      await api.createWorkspace("My WS");
      await api.createWorkspace("My WS", "desc");
      await api.fetchWorkspaceDetail("ws-1");
      expect(globalThis.fetch).toHaveBeenCalledTimes(6);
    });

    it("repairWorkspaceMemory POST", async () => {
      await api.repairWorkspaceMemory("ws-1");
      expect((globalThis.fetch as any).mock.calls[0][1].method).toBe("POST");
    });

    it("updateWorkspace / deleteWorkspace / transferWorkspaceOwnership", async () => {
      await api.updateWorkspace("ws-1", { name: "n", expected_updated_at: "t" });
      await api.deleteWorkspace("ws-1");
      await api.transferWorkspaceOwnership("ws-1", "u-2");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["PUT", "DELETE", "POST"]);
    });

    it("inviteWorkspaceMember / updateWorkspaceMemberRole / removeWorkspaceMember / leaveWorkspace", async () => {
      await api.inviteWorkspaceMember("ws", "e@x.com", "viewer");
      await api.updateWorkspaceMemberRole("ws", "u", "editor");
      await api.removeWorkspaceMember("ws", "u");
      await api.leaveWorkspace("ws");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["POST", "PUT", "DELETE", "POST"]);
    });

    it("listWorkspaceInvitations / revokeWorkspaceInvitation / verifyInvitation / acceptInvitation", async () => {
      await api.listWorkspaceInvitations("ws");
      await api.revokeWorkspaceInvitation("ws", "tok");
      await api.verifyInvitation("tok");
      await api.acceptInvitation("tok");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["GET", "DELETE", "GET", "POST"]);
    });
  });

  describe("Histories + storage", () => {
    it("fetchAgentHistory parses content", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({ content: "[1,2]" }));
      await expect(api.fetchAgentHistory("a")).resolves.toEqual([1, 2]);
    });

    it("fetchAgentHistory returns null on failure", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.fetchAgentHistory("a")).resolves.toBeNull();
    });

    it("putAgentHistory true/false", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({}));
      await expect(api.putAgentHistory("a", [1])).resolves.toBe(true);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.putAgentHistory("a", [1])).resolves.toBe(false);
    });

    it("fetchSkillHistory + putSkillHistory parallel paths", async () => {
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({ content: "[3]" }));
      await expect(api.fetchSkillHistory("s")).resolves.toEqual([3]);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.fetchSkillHistory("s")).resolves.toBeNull();
      (globalThis.fetch as any).mockResolvedValueOnce(mockJson({}));
      await expect(api.putSkillHistory("s", [1])).resolves.toBe(true);
      (globalThis.fetch as any).mockResolvedValueOnce({
        ok: false, status: 500, json: () => Promise.resolve({}),
      });
      await expect(api.putSkillHistory("s", [1])).resolves.toBe(false);
    });

    it("Chat sessions: list/get/put/delete with success+failure", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({ items: [{ id: "s1" }] }));
      await expect(api.listChatSessions("ak")).resolves.toEqual([{ id: "s1" }]);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.listChatSessions("ak")).resolves.toEqual([]);
      fetchMock.mockResolvedValueOnce(mockJson({ messages: [] }));
      await expect(api.getChatSession("ak", "s")).resolves.toEqual({ messages: [] });
      fetchMock.mockResolvedValueOnce({ ok: false, status: 404, json: () => Promise.resolve({}) });
      await expect(api.getChatSession("ak", "s")).resolves.toBeNull();
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.getChatSession("ak", "s")).resolves.toBeNull();
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.putChatSession("ak", "s", { x: 1 })).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.putChatSession("ak", "s", { x: 1 })).resolves.toBe(false);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteChatSession("ak", "s")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.deleteChatSession("ak", "s")).resolves.toBe(false);
    });

    it("getStorage / putStorage / deleteStorage", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({ data: { foo: 1 } }));
      await expect(api.getStorage("k")).resolves.toEqual({ foo: 1 });
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.getStorage("k")).resolves.toBeNull();
      fetchMock.mockResolvedValueOnce(mockJson({ key: "k" }));
      await expect(api.putStorage("k", { v: 1 })).resolves.toBe("k");
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.putStorage("k", { v: 1 })).resolves.toBe("k"); // falls back to provided key
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.putStorage("k", { v: 1 })).resolves.toBeNull();
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteStorage("k")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.deleteStorage("k")).resolves.toBe(false);
    });
  });

  describe("Agent skill files", () => {
    it("fetchAgentSkillFiles / fetchAgentSkillFilesBulk / fetchAgentSkillFile fall back gracefully", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({ files: ["a"] }));
      await expect(api.fetchAgentSkillFiles("a", "s")).resolves.toEqual(["a"]);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.fetchAgentSkillFiles("a", "s")).resolves.toEqual([]);
      fetchMock.mockResolvedValueOnce(mockJson({ files: { a: "x" } }));
      await expect(api.fetchAgentSkillFilesBulk("a", "s")).resolves.toEqual({ a: "x" });
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.fetchAgentSkillFilesBulk("a", "s")).resolves.toEqual({});
      fetchMock.mockResolvedValueOnce(mockJson({ content: "BODY" }));
      await expect(api.fetchAgentSkillFile("a", "s", "f")).resolves.toBe("BODY");
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.fetchAgentSkillFile("a", "s", "f")).resolves.toBeNull();
    });

    it("copySkillFilesFrom / putAgentSkillFile / deleteAgentSkillFiles", async () => {
      const fetchMock = globalThis.fetch as any;
      fetchMock.mockResolvedValueOnce(mockJson({ copied: 5 }));
      await expect(api.copySkillFilesFrom("a", "s", "a2", "s2")).resolves.toBe(5);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.copySkillFilesFrom("a", "s", "a2", "s2")).resolves.toBe(0);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.putAgentSkillFile("a", "s", "f", "c")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.putAgentSkillFile("a", "s", "f", "c")).resolves.toBe(false);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteAgentSkillFiles("a", "s")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce(mockJson({}));
      await expect(api.deleteAgentSkillFiles("a", "s", "x")).resolves.toBe(true);
      fetchMock.mockResolvedValueOnce({ ok: false, status: 500, json: () => Promise.resolve({}) });
      await expect(api.deleteAgentSkillFiles("a", "s")).resolves.toBe(false);
    });
  });

  describe("Memory + costs + roles + traces + KB", () => {
    beforeEach(() => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ sessions: [], agents: [], items: [] }));
    });

    it("listMyMemories / loadMoreMemories / deleteMyMemory / forgetAllMemories", async () => {
      await api.listMyMemories("ws", "a");
      await api.loadMoreMemories("ws", "a", "facts", "t");
      await api.deleteMyMemory("ws", "a", "rid");
      await api.deleteMyMemory("ws", "a", "rid", "facts");
      await api.forgetAllMemories("ws", "a");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["GET", "GET", "DELETE", "DELETE", "DELETE"]);
    });

    it("createWorkspaceRole / getWorkspacePermissions / grantMcpTargets / revokeMcpTargets", async () => {
      await api.createWorkspaceRole("ws-1");
      await api.getWorkspacePermissions("ws-1", ["s3:GetObject", "lambda:InvokeFunction"]);
      await api.grantMcpTargets("ws-1", ["t1", "t2"]);
      await api.revokeMcpTargets("ws-1", ["t1"]);
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toEqual(["POST", "GET", "POST", "POST"]);
      const permsUrl = (globalThis.fetch as any).mock.calls[1][0];
      expect(permsUrl).toContain("actions=s3%3AGetObject");
    });

    it("fetchWorkspaceCosts / fetchAgentCosts / fetchAdminCosts / getWorkspaceCosts", async () => {
      await api.fetchWorkspaceCosts();
      await api.fetchWorkspaceCosts("30d");
      await api.fetchAgentCosts("a");
      await api.fetchAgentCosts("a", "24h");
      await api.fetchAdminCosts();
      await api.getWorkspaceCosts();
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods.every((m: string) => m === "GET")).toBe(true);
    });

    it("listTraces / getSessionTrace / getTraceStats", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({ sessions: [{ sessionId: "s" }] }));
      await expect(api.listTraces("a")).resolves.toEqual([{ sessionId: "s" }]);
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await expect(api.listTraces("a")).resolves.toEqual([]);
      await api.getSessionTrace("a", "s");
      await api.getTraceStats("a");
      expect((globalThis.fetch as any).mock.calls.length).toBeGreaterThanOrEqual(3);
    });

    it("KB endpoints", async () => {
      (globalThis.fetch as any).mockResolvedValue(mockJson({}));
      await api.fetchKnowledgeBases();
      await api.fetchKnowledgeBase("kb1");
      await api.createKnowledgeBase("n", "d");
      await api.deleteKnowledgeBase("kb1");
      await api.uploadKBDocument("kb1", "stage/key", "f.pdf");
      await api.deleteKBDocument("kb1", "doc-key");
      await api.fetchKBIngestion("kb1");
      await api.attachKnowledgeBase("a1", "kb1");
      await api.detachKnowledgeBase("a1", "kb1");
      const methods = (globalThis.fetch as any).mock.calls.map((c: any) => c[1].method);
      expect(methods).toContain("GET");
      expect(methods).toContain("POST");
      expect(methods).toContain("DELETE");
    });
  });

  describe("ApiError class", () => {
    it("exposes status and body", () => {
      const e = new api.ApiError(418, { error: "teapot", code: "TP" });
      expect(e.status).toBe(418);
      expect(e.body).toEqual({ error: "teapot", code: "TP" });
      expect(e.message).toContain("418");
    });

    it("falls back when body has no error", () => {
      const e = new api.ApiError(500, {});
      expect(e.message).toContain("API error");
    });
  });
});
