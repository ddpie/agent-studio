import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

const WS_KEY = "agent-studio-workspace-id";
const API_BASE = agentConfig.apiUrl; // 空字符串 = 相对路径（生产），有值 = 直连 CloudFront（开发）

export function getWorkspaceId(): string {
  return localStorage.getItem(WS_KEY) || "default";
}

export function setWorkspaceId(wsId: string): void {
  localStorage.setItem(WS_KEY, wsId);
}

/**
 * 确保 localStorage 中有有效的 workspace ID。
 * 如果没有，从 API 获取用户的 workspace 列表并设置第一个。
 */
export async function ensureWorkspaceId(): Promise<string> {
  const existing = localStorage.getItem(WS_KEY);
  if (existing && existing !== "default") return existing;

  try {
    const resp = await apiGetRaw<{ items: Array<{ workspaceId: string }> }>("/api/workspaces");
    if (resp.items?.length) {
      const wsId = resp.items[0].workspaceId;
      setWorkspaceId(wsId);
      return wsId;
    }
  } catch (err) {
    console.error("Failed to fetch workspaces for auto-setup:", err);
  }
  return existing || "default";
}

async function getIdToken(forceRefresh = false): Promise<string> {
  const session = await fetchAuthSession({ forceRefresh });
  const token = session.tokens?.idToken?.toString();
  if (!token) throw new Error("Not authenticated");
  return token;
}

export class ApiError extends Error {
  status: number;
  body: { error?: string; code?: string };

  constructor(status: number, body: { error?: string; code?: string }) {
    super(`${status}: ${body?.error || "API error"}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function request<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getIdToken();
  const wsId = getWorkspaceId();
  const url = `${API_BASE}/api/workspaces/${wsId}${path}`;

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const init: RequestInit = { method, headers };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (err) {
    throw new ApiError(0, { error: "Network error" });
  }

  // 401 → token 可能过期，强制刷新后重试一次
  if (resp.status === 401) {
    const freshToken = await getIdToken(true);
    headers.Authorization = `Bearer ${freshToken}`;
    try {
      resp = await fetch(url, { ...init, headers });
    } catch (err) {
      throw new ApiError(0, { error: "Network error" });
    }
  }

  if (!resp.ok) {
    const errBody = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, errBody);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

/** 不带 workspace 前缀的请求（如 /api/public/*） */
async function requestRaw<T = unknown>(method: string, fullPath: string, body?: unknown): Promise<T> {
  if (!fullPath.startsWith("/api")) throw new Error("requestRaw only supports /api paths");
  const token = await getIdToken();
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const init: RequestInit = { method, headers };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${fullPath}`, init);
  } catch (err) {
    throw new ApiError(0, { error: "Network error" });
  }
  if (!resp.ok) {
    const errBody = await resp.json().catch(() => ({}));
    throw new ApiError(resp.status, errBody);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

// ── 便捷方法 ──

export const apiGet = <T = unknown>(path: string) => request<T>("GET", path);
export const apiPost = <T = unknown>(path: string, body?: unknown) => request<T>("POST", path, body);
export const apiPut = <T = unknown>(path: string, body?: unknown) => request<T>("PUT", path, body);
export const apiDelete = <T = unknown>(path: string) => request<T>("DELETE", path);
export const apiGetRaw = <T = unknown>(path: string) => requestRaw<T>("GET", path);

// ── 类型定义 ──

export interface AgentListItem {
  agentId: string;
  name: string;
  display_name?: string;
  description?: string;
  status: string;
  visibility?: string;
  model_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ToolItem {
  toolId: string;
  name: string;
  description?: string;
  category?: string;
  code?: string;
  builtin?: boolean;
  owner?: string;
  visibility?: string;
  created_at?: string;
  updated_at?: string;
  deleted?: boolean;
  deleted_at?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  nextCursor?: string;
}

// ── Agent 相关 ──

export async function fetchAgents(cursor?: string, limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGet<PaginatedResponse<AgentListItem>>(`/agents?${params}`);
}

export async function fetchAgent(agentId: string) {
  return apiGet<Record<string, any>>(`/agents/${encodeURIComponent(agentId)}`);
}

export async function createAgent(data: Record<string, any>) {
  return apiPost<{ agentId: string }>("/agents", data);
}

export async function updateAgent(agentId: string, data: Record<string, any>) {
  return apiPut(`/agents/${encodeURIComponent(agentId)}`, data);
}

export async function deleteAgent(agentId: string) {
  return apiDelete(`/agents/${encodeURIComponent(agentId)}`);
}

export async function fetchAgentFile(agentId: string, filePath: string): Promise<string> {
  try {
    const data = await apiGet<{ content?: string }>(
      `/agents/${encodeURIComponent(agentId)}/files?path=${encodeURIComponent(filePath)}`
    );
    return data.content || "";
  } catch {
    return "";
  }
}

export async function putAgentFile(agentId: string, filePath: string, content: string) {
  return apiPut(
    `/agents/${encodeURIComponent(agentId)}/files?path=${encodeURIComponent(filePath)}`,
    { content }
  );
}

// ── 上传 ──

export async function getImageUploadUrl(filename: string, contentType: string) {
  return apiPost<{ uploadUrl: string; fields: Record<string, string>; s3Key: string }>(
    "/uploads/images", { filename, content_type: contentType }
  );
}

export async function getAttachmentUploadUrl(filename: string, contentType: string, sessionId: string) {
  return apiPost<{ uploadUrl: string; fields: Record<string, string>; s3Key: string }>(
    "/uploads/attachments", { filename, content_type: contentType, sessionId }
  );
}

/** 用 presigned POST 上传文件到 S3 */
export async function uploadWithPresignedPost(
  presigned: { url: string; fields: Record<string, string> },
  file: Blob
): Promise<void> {
  const formData = new FormData();
  for (const [k, v] of Object.entries(presigned.fields)) {
    formData.append(k, v);
  }
  formData.append("file", file);
  const resp = await fetch(presigned.url, { method: "POST", body: formData });
  if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`);
}

// ── 下载 ──

export async function getDownloadUrl(s3Key: string): Promise<string | null> {
  try {
    const resp = await apiGet<{ url: string }>(`/downloads?key=${encodeURIComponent(s3Key)}`);
    return resp.url;
  } catch (err) {
    console.error("getDownloadUrl failed:", err);
    return null;
  }
}

// ── 工具（带缓存） ──

let _toolsCache: { items: ToolItem[]; ts: number } | null = null;
const TOOLS_CACHE_TTL = 5 * 60 * 1000; // 5 分钟

export async function fetchTools(cursor?: string, limit = 100) {
  if (!cursor && _toolsCache && Date.now() - _toolsCache.ts < TOOLS_CACHE_TTL) {
    return { items: _toolsCache.items } as PaginatedResponse<ToolItem>;
  }
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const resp = await apiGet<PaginatedResponse<ToolItem>>(`/tools?${params}`);
  if (!cursor) _toolsCache = { items: resp.items, ts: Date.now() };
  return resp;
}

export function invalidateToolsCache() { _toolsCache = null; }

export async function createOrUpdateTool(data: Record<string, any>): Promise<Record<string, any>> {
  try {
    invalidateToolsCache();
    if (data.toolId) {
      return await apiPut(`/tools/${encodeURIComponent(data.toolId)}`, data);
    }
    return await apiPost<{ toolId: string }>("/tools", data);
  } catch (err) {
    console.error("createOrUpdateTool failed:", err);
    throw err;
  }
}

export async function deleteToolApi(toolId: string): Promise<boolean> {
  try {
    invalidateToolsCache();
    await apiDelete(`/tools/${encodeURIComponent(toolId)}`);
    return true;
  } catch (err) {
    console.error("deleteToolApi failed:", err);
    return false;
  }
}

export async function restoreToolApi(toolId: string): Promise<boolean> {
  try {
    invalidateToolsCache();
    await apiPost(`/tools/${encodeURIComponent(toolId)}/restore`);
    return true;
  } catch (err) {
    console.error("restoreToolApi failed:", err);
    return false;
  }
}

export async function permanentDeleteToolApi(toolId: string): Promise<boolean> {
  try {
    invalidateToolsCache();
    await apiDelete(`/tools/${encodeURIComponent(toolId)}/permanent`);
    return true;
  } catch (err) {
    console.error("permanentDeleteToolApi failed:", err);
    return false;
  }
}

export async function fetchDeletedTools(): Promise<ToolItem[]> {
  try {
    return await apiGet<ToolItem[]>("/tools/deleted");
  } catch (err) {
    console.error("fetchDeletedTools failed:", err);
    return [];
  }
}

// ── 公开资源 ──

export async function fetchPublicAgents(cursor?: string, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGetRaw<PaginatedResponse<AgentListItem>>(`/api/public/agents?${params}`);
}

// ── 技能文件 ──

export async function fetchSkills(cursor?: string, limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGet<PaginatedResponse<Record<string, any>>>(`/skills?${params}`);
}

export async function fetchDeletedSkills(cursor?: string, limit = 50) {
  const params = new URLSearchParams({ limit: String(limit), deleted: "true" });
  if (cursor) params.set("cursor", cursor);
  return apiGet<PaginatedResponse<Record<string, any>>>(`/skills?${params}`);
}

export async function fetchSkillFiles(skillId: string): Promise<string[]> {
  try {
    const resp = await apiGet<{ files: string[] }>(`/skills/${encodeURIComponent(skillId)}/files`);
    return resp.files;
  } catch (err) {
    console.error("fetchSkillFiles failed:", err);
    return [];
  }
}

export async function fetchSkillFile(skillId: string, path: string): Promise<string | null> {
  try {
    const resp = await apiGet<{ content: string }>(
      `/skills/${encodeURIComponent(skillId)}/files?path=${encodeURIComponent(path)}`
    );
    return resp.content;
  } catch (err) {
    console.error("fetchSkillFile failed:", err);
    return null;
  }
}

export async function putSkillFile(skillId: string, path: string, content: string): Promise<boolean> {
  try {
    await apiPut(
      `/skills/${encodeURIComponent(skillId)}/files?path=${encodeURIComponent(path)}`,
      { content }
    );
    return true;
  } catch (err) {
    console.error("putSkillFile failed:", err);
    return false;
  }
}

export async function deleteSkillFileApi(skillId: string, path: string): Promise<boolean> {
  try {
    await apiDelete(`/skills/${encodeURIComponent(skillId)}/files?path=${encodeURIComponent(path)}`);
    return true;
  } catch (err) {
    console.error("deleteSkillFileApi failed:", err);
    return false;
  }
}

export async function importSkillApi(body: Record<string, any>): Promise<Record<string, any> | null> {
  try {
    return await apiPost<Record<string, any>>("/skills/import", body);
  } catch (err) {
    console.error("importSkillApi failed:", err);
    return null;
  }
}

export async function restoreSkillApi(skillId: string): Promise<boolean> {
  try {
    await apiPost(`/skills/${encodeURIComponent(skillId)}/restore`);
    return true;
  } catch (err) {
    console.error("restoreSkillApi failed:", err);
    return false;
  }
}

export async function permanentlyDeleteSkillApi(skillId: string): Promise<boolean> {
  try {
    await apiDelete(`/skills/${encodeURIComponent(skillId)}?purge=true`);
    return true;
  } catch (err) {
    console.error("permanentlyDeleteSkillApi failed:", err);
    return false;
  }
}

export async function deleteSkillApi(skillId: string): Promise<boolean> {
  try {
    await apiDelete(`/skills/${encodeURIComponent(skillId)}`);
    return true;
  } catch (err) {
    console.error("deleteSkillApi failed:", err);
    return false;
  }
}

// ── Agent history（领域端点）──

export async function fetchAgentHistory(agentId: string): Promise<unknown[] | null> {
  try {
    const resp = await apiGet<{ content: string }>(
      `/agents/${encodeURIComponent(agentId)}/files?path=assistant-history.json`
    );
    return JSON.parse(resp.content);
  } catch {
    console.error("fetchAgentHistory failed");
    return null;
  }
}

export async function putAgentHistory(agentId: string, data: unknown[]): Promise<boolean> {
  try {
    await apiPut(
      `/agents/${encodeURIComponent(agentId)}/files?path=assistant-history.json`,
      { content: JSON.stringify(data) }
    );
    return true;
  } catch {
    console.error("putAgentHistory failed");
    return false;
  }
}

// ── Skill history（領域端点）──

export async function fetchSkillHistory(skillId: string): Promise<unknown[] | null> {
  try {
    const resp = await apiGet<{ content: string }>(
      `/skills/${encodeURIComponent(skillId)}/files?path=assistant-history.json`
    );
    return JSON.parse(resp.content);
  } catch {
    console.error("fetchSkillHistory failed");
    return null;
  }
}

export async function putSkillHistory(skillId: string, data: unknown[]): Promise<boolean> {
  try {
    await apiPut(
      `/skills/${encodeURIComponent(skillId)}/files?path=assistant-history.json`,
      { content: JSON.stringify(data) }
    );
    return true;
  } catch {
    console.error("putSkillHistory failed");
    return false;
  }
}

// ── Workspace-scoped storage ──

export async function getStorage<T = unknown>(key: string): Promise<T | null> {
  try {
    const resp = await apiGet<{ data: T | null }>(`/storage?key=${encodeURIComponent(key)}`);
    return resp.data;
  } catch {
    console.error("getStorage failed");
    return null;
  }
}

export async function putStorage(key: string, data: unknown): Promise<boolean> {
  try {
    await apiPut("/storage", { key, data });
    return true;
  } catch {
    console.error("putStorage failed");
    return false;
  }
}

export async function deleteStorage(key: string): Promise<boolean> {
  try {
    await apiDelete(`/storage?key=${encodeURIComponent(key)}`);
    return true;
  } catch {
    console.error("deleteStorage failed");
    return false;
  }
}

// ── Agent 技能文件 ──

export async function fetchAgentSkillFiles(agentId: string, skillId: string): Promise<string[]> {
  try {
    const resp = await apiGet<{ files: string[] }>(
      `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/files`
    );
    return resp.files;
  } catch (err) {
    console.error("fetchAgentSkillFiles failed:", err);
    return [];
  }
}

export async function fetchAgentSkillFilesBulk(agentId: string, skillId: string): Promise<Record<string, string>> {
  try {
    const resp = await apiGet<{ files: Record<string, string> }>(
      `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/files?bulk=true`
    );
    return resp.files || {};
  } catch (err) {
    console.error("fetchAgentSkillFilesBulk failed:", err);
    return {};
  }
}

export async function fetchAgentSkillFile(agentId: string, skillId: string, path: string): Promise<string | null> {
  try {
    const resp = await apiGet<{ content: string }>(
      `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/files?path=${encodeURIComponent(path)}`
    );
    return resp.content;
  } catch (err) {
    console.error("fetchAgentSkillFile failed:", err);
    return null;
  }
}

export async function putAgentSkillFile(agentId: string, skillId: string, path: string, content: string): Promise<boolean> {
  try {
    await apiPut(
      `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/files?path=${encodeURIComponent(path)}`,
      { content }
    );
    return true;
  } catch (err) {
    console.error("putAgentSkillFile failed:", err);
    return false;
  }
}

export async function deleteAgentSkillFiles(agentId: string, skillId: string, path?: string): Promise<boolean> {
  try {
    const url = `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/files`
      + (path ? `?path=${encodeURIComponent(path)}` : "");
    await apiDelete(url);
    return true;
  } catch (err) {
    console.error("deleteAgentSkillFiles failed:", err);
    return false;
  }
}
