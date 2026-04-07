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
  return apiPost<{ url: string; fields: Record<string, string>; key: string }>(
    "/uploads/images", { filename, content_type: contentType }
  );
}

export async function getAttachmentUploadUrl(filename: string, contentType: string) {
  return apiPost<{ url: string; fields: Record<string, string>; key: string }>(
    "/uploads/attachments", { filename, content_type: contentType }
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

// ── 公开资源 ──

export async function fetchPublicAgents(cursor?: string, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGetRaw<PaginatedResponse<AgentListItem>>(`/api/public/agents?${params}`);
}
