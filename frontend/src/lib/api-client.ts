import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

const WS_KEY = "agent-studio-workspace-id";
const API_BASE = agentConfig.apiUrl; // 空字符串 = 相对路径（生产），有值 = 直连 CloudFront（开发）

export function getWorkspaceId(): string {
  return localStorage.getItem(WS_KEY) || "default";
}

export function setWorkspaceId(wsId: string): void {
  const prev = localStorage.getItem(WS_KEY);
  localStorage.setItem(WS_KEY, wsId);
  // Workspace changed — clear workspace-scoped persisted state (chat sessions,
  // currentAgentId, lastActiveSessionByAgent) so stale agent IDs don't leak
  // across workspaces. UI prefs (`agent-studio-ui`) and the workspace pointer
  // itself (`agent-studio-workspace-id`) are intentionally preserved.
  if (prev && prev !== wsId) {
    // Dynamic import to avoid a static cycle (chat-store -> agentcore-client
    // -> api-client). Fire-and-forget: the reset is best-effort and any error
    // is swallowed — worst case persisted state survives until next reload.
    import("../stores/chat-store")
      .then((mod) => mod.resetChatForWorkspaceSwitch())
      .catch(() => { /* ignore */ });
  }
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
async function requestRaw<T = unknown>(
  method: string,
  fullPath: string,
  body?: unknown,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  if (!fullPath.startsWith("/api")) throw new Error("requestRaw only supports /api paths");
  const token = await getIdToken();
  const headers: Record<string, string> = { Authorization: `Bearer ${token}`, ...(extraHeaders || {}) };
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
export const apiPostRaw = <T = unknown>(path: string, body?: unknown, headers?: Record<string, string>) =>
  requestRaw<T>("POST", path, body, headers);
export const apiPutRaw = <T = unknown>(path: string, body?: unknown) => requestRaw<T>("PUT", path, body);
export const apiDeleteRaw = <T = unknown>(path: string) => requestRaw<T>("DELETE", path);

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

// ── A2A per-user keys ──

export interface A2aKey {
  keyId: string;
  keyPrefix: string;
  createdAt: string;
  lastUsedAt?: string;
  revoked: boolean;
}

export interface A2aKeyCreated extends A2aKey {
  apiKey: string;
}

export async function listA2aKeys(agentId: string): Promise<A2aKey[]> {
  const resp = await apiGet<{ keys?: A2aKey[] }>(
    `/agents/${encodeURIComponent(agentId)}/a2a-keys`
  );
  return resp.keys ?? [];
}

export async function createA2aKey(agentId: string): Promise<A2aKeyCreated> {
  return apiPost<A2aKeyCreated>(`/agents/${encodeURIComponent(agentId)}/a2a-keys`, {});
}

export async function revokeA2aKey(agentId: string, keyId: string): Promise<void> {
  await apiDelete(
    `/agents/${encodeURIComponent(agentId)}/a2a-keys/${encodeURIComponent(keyId)}`
  );
}

export async function listMetaA2aKeys(): Promise<A2aKey[]> {
  const resp = await apiGet<{ keys?: A2aKey[] }>(`/meta-agent/a2a-keys`);
  return resp.keys ?? [];
}

export async function createMetaA2aKey(): Promise<A2aKeyCreated> {
  return apiPost<A2aKeyCreated>(`/meta-agent/a2a-keys`, {});
}

export async function revokeMetaA2aKey(keyId: string): Promise<void> {
  await apiDelete(`/meta-agent/a2a-keys/${encodeURIComponent(keyId)}`);
}

export function getPublicAgentCardUrl(agentId: string): string {
  return `${window.location.origin}/a2a/agents/${encodeURIComponent(agentId)}/.well-known/agent-card.json`;
}

export function getA2aEndpointUrl(agentId: string): string {
  return `${window.location.origin}/a2a/agents/${encodeURIComponent(agentId)}`;
}

export function getMetaA2aCardUrl(): string {
  return `${window.location.origin}/a2a/meta-agent/.well-known/agent-card.json`;
}

export function getMetaA2aEndpointUrl(): string {
  return `${window.location.origin}/a2a/meta-agent`;
}

// ── Schedules (EventBridge Scheduler) ──

export interface AgentSchedule {
  name: string;
  suffix: string;
  cron: string;
  state?: string;
  arn?: string;
  groupName?: string;
  createdAt?: string;
  lastModifiedAt?: string;
}

export interface AgentScheduleCreated extends AgentSchedule {
  arn: string;
}

export async function listAgentSchedules(agentId: string): Promise<AgentSchedule[]> {
  const resp = await apiGet<{ schedules?: AgentSchedule[] }>(
    `/agents/${encodeURIComponent(agentId)}/schedules`
  );
  return resp.schedules ?? [];
}

export async function createAgentSchedule(
  agentId: string,
  body: { name: string; cron: string; prompt: string }
): Promise<AgentScheduleCreated> {
  return apiPost<AgentScheduleCreated>(
    `/agents/${encodeURIComponent(agentId)}/schedules`,
    body
  );
}

export async function deleteAgentSchedule(agentId: string, name: string): Promise<void> {
  await apiDelete(
    `/agents/${encodeURIComponent(agentId)}/schedules/${encodeURIComponent(name)}`
  );
}

// ── Agent runtime (AgentCore Control Plane passthrough) ──

export interface AgentRuntimeInfo {
  status: "CREATING" | "CREATE_FAILED" | "UPDATING" | "UPDATE_FAILED" | "READY" | "DELETING";
  lastUpdatedAt?: string;
  description?: string;
  agentRuntimeVersion?: string;
  agentRuntimeName?: string;
}

export async function getAgentRuntime(agentId: string): Promise<AgentRuntimeInfo> {
  return apiGet<AgentRuntimeInfo>(`/agents/${encodeURIComponent(agentId)}/runtime`);
}

export interface AgentRuntimeVersion {
  agentRuntimeVersion: string;
  status: string;
  lastUpdatedAt?: string;
  description?: string;
  agentRuntimeName?: string;
}

export async function listAgentVersions(agentId: string): Promise<AgentRuntimeVersion[]> {
  const resp = await apiGet<{ versions?: AgentRuntimeVersion[] }>(
    `/agents/${encodeURIComponent(agentId)}/versions`
  );
  return resp.versions ?? [];
}

export interface AgentEndpoint {
  name: string;
  liveVersion?: string;
  targetVersion?: string;
  status: string;
  createdAt?: string;
  lastUpdatedAt?: string;
  description?: string;
}

export async function listAgentEndpoints(agentId: string): Promise<AgentEndpoint[]> {
  const resp = await apiGet<{ endpoints?: AgentEndpoint[] }>(
    `/agents/${encodeURIComponent(agentId)}/endpoints`
  );
  return resp.endpoints ?? [];
}

export async function createAgentEndpoint(agentId: string, body: { name: string; version: string }) {
  return apiPost(`/agents/${encodeURIComponent(agentId)}/endpoints`, body);
}

export async function updateAgentEndpoint(agentId: string, endpointName: string, version: string) {
  return apiPut(
    `/agents/${encodeURIComponent(agentId)}/endpoints/${encodeURIComponent(endpointName)}`,
    { version }
  );
}

export async function deleteAgentEndpoint(agentId: string, endpointName: string) {
  return apiDelete(`/agents/${encodeURIComponent(agentId)}/endpoints/${encodeURIComponent(endpointName)}`);
}

// ── Agent evaluations (Sprint 2 F5) ──

export interface AgentEvaluation {
  timestamp: string;
  evaluator: string;
  score: number;
  sessionId?: string;
  traceId?: string;
  reason?: string;
}

export async function listAgentEvaluations(agentId: string): Promise<AgentEvaluation[]> {
  const resp = await apiGet<{ evaluations?: AgentEvaluation[] }>(
    `/agents/${encodeURIComponent(agentId)}/evaluations`
  );
  return resp.evaluations ?? [];
}

// ── Traces (Sprint 2 F4) ──

export interface TraceSummary {
  sessionId: string;
  traceId?: string;
  firstEvent?: string;
  spanCount: number;
}

export interface TraceSpan {
  spanId: string;
  parentSpanId: string | null;
  name: string;
  startMs: number;
  durationMs: number;
  status: string;
  children: TraceSpan[];
}

export async function listTraces(agentId: string): Promise<TraceSummary[]> {
  const resp = await apiGet<{ sessions?: TraceSummary[] }>(
    `/agents/${encodeURIComponent(agentId)}/traces`
  );
  return resp.sessions ?? [];
}

export async function getSessionTrace(agentId: string, sessionId: string): Promise<TraceSpan | null> {
  const resp = await apiGet<{ root?: TraceSpan }>(
    `/agents/${encodeURIComponent(agentId)}/traces/${encodeURIComponent(sessionId)}`
  );
  return resp.root ?? null;
}

// ── Meta-Agent AgentCard (Sprint 2 F1c) ──

export interface AgentCardSkill {
  id: string;
  name?: string;
  description?: string;
  tags?: string[];
}

export interface AgentCard {
  name?: string;
  description?: string;
  url?: string;
  version?: string;
  protocolVersion?: string;
  preferredTransport?: string;
  defaultInputModes?: string[];
  defaultOutputModes?: string[];
  capabilities?: Record<string, unknown>;
  skills?: AgentCardSkill[];
  runtimeArn?: string;
  runtimeStatus?: string;
}

export async function getMetaAgentCard(): Promise<AgentCard | null> {
  try {
    return await apiGet<AgentCard>(`/meta-agent/agent-card`);
  } catch {
    return null;
  }
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

// ── 公开资源 / Marketplace ──

export interface PublicAgentItem {
  agentId: string;
  name: string;
  description?: string;
  model_id?: string;
  supports_images?: boolean;
  welcome_message?: string;
  created_at?: string;
}

export interface PublicSkillItem {
  skillId: string;
  name: string;
  description?: string;
  type?: string;
  tags?: string[];
  created_at?: string;
}

export interface PublicToolItem {
  toolId: string;
  name: string;
  description?: string;
  category?: string;
  created_at?: string;
}

export async function fetchPublicAgents(cursor?: string, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGetRaw<PaginatedResponse<PublicAgentItem>>(`/api/public/agents?${params}`);
}

export async function fetchPublicSkills(cursor?: string, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGetRaw<PaginatedResponse<PublicSkillItem>>(`/api/public/skills?${params}`);
}

export async function fetchPublicTools(cursor?: string, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return apiGetRaw<PaginatedResponse<PublicToolItem>>(`/api/public/tools?${params}`);
}

/**
 * Clone helpers — copy a published resource into the caller's current workspace.
 * Target workspace is pulled from localStorage and sent via `x-workspace-id` header.
 */
export async function clonePublicAgent(agentId: string, name?: string) {
  const wsId = getWorkspaceId();
  return apiPostRaw<{ agentId: string; name: string; workspace_id: string }>(
    `/api/public/agents/${encodeURIComponent(agentId)}/clone`,
    name ? { name } : {},
    { "x-workspace-id": wsId },
  );
}

export async function clonePublicSkill(skillId: string) {
  const wsId = getWorkspaceId();
  return apiPostRaw<{ skillId: string; workspace_id: string }>(
    `/api/public/skills/${encodeURIComponent(skillId)}/clone`,
    {},
    { "x-workspace-id": wsId },
  );
}

export async function clonePublicTool(toolId: string) {
  const wsId = getWorkspaceId();
  return apiPostRaw<{ toolId: string; workspace_id: string }>(
    `/api/public/tools/${encodeURIComponent(toolId)}/clone`,
    {},
    { "x-workspace-id": wsId },
  );
}

// ── Publish / Unpublish (workspace-scoped) ──

export async function publishAgent(agentId: string) {
  return apiPost<{ agentId: string; visibility: string }>(
    `/agents/${encodeURIComponent(agentId)}/publish`,
  );
}

export async function unpublishAgent(agentId: string) {
  return apiPost<{ agentId: string; visibility: string }>(
    `/agents/${encodeURIComponent(agentId)}/unpublish`,
  );
}

export async function publishSkill(skillId: string) {
  return apiPost<{ skillId: string; visibility: string }>(
    `/skills/${encodeURIComponent(skillId)}/publish`,
  );
}

export async function unpublishSkill(skillId: string) {
  return apiPost<{ skillId: string; visibility: string }>(
    `/skills/${encodeURIComponent(skillId)}/unpublish`,
  );
}

export async function publishTool(toolId: string) {
  return apiPost<{ toolId: string; visibility: string }>(
    `/tools/${encodeURIComponent(toolId)}/publish`,
  );
}

export async function unpublishTool(toolId: string) {
  return apiPost<{ toolId: string; visibility: string }>(
    `/tools/${encodeURIComponent(toolId)}/unpublish`,
  );
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

// ── Workspace members & invitations ──

export interface WorkspaceMember {
  userId: string;
  role: "viewer" | "editor" | "admin" | "owner";
  joined_at: string;
  display_name?: string;
}

export interface WorkspaceDetail {
  workspaceId: string;
  name: string;
  description?: string;
  owner_id?: string;
  created_at?: string;
  updated_at?: string;
  members: WorkspaceMember[];
}

export interface WorkspaceInvitation {
  token: string;
  email: string;
  role: "viewer" | "editor" | "admin";
  invited_by?: string;
  created_at?: string;
}

export async function fetchWorkspaces() {
  return apiGetRaw<{
    items: Array<{
      workspaceId: string;
      name?: string;
      description?: string;
      role?: string;
      created_at?: string;
    }>;
  }>("/api/workspaces");
}

export async function fetchWorkspaceDetail(wsId: string): Promise<WorkspaceDetail> {
  return apiGetRaw<WorkspaceDetail>(`/api/workspaces/${encodeURIComponent(wsId)}`);
}

export async function inviteWorkspaceMember(
  wsId: string,
  email: string,
  role: "viewer" | "editor" | "admin"
) {
  return apiPostRaw<{ token: string; email: string; role: string; created_at: string }>(
    `/api/workspaces/${encodeURIComponent(wsId)}/members`,
    { email, role }
  );
}

export async function updateWorkspaceMemberRole(
  wsId: string,
  memberId: string,
  role: "viewer" | "editor" | "admin"
) {
  return apiPutRaw<{ userId: string; role: string }>(
    `/api/workspaces/${encodeURIComponent(wsId)}/members/${encodeURIComponent(memberId)}`,
    { role }
  );
}

export async function removeWorkspaceMember(wsId: string, memberId: string) {
  return apiDeleteRaw<{ removed: boolean }>(
    `/api/workspaces/${encodeURIComponent(wsId)}/members/${encodeURIComponent(memberId)}`
  );
}

export async function leaveWorkspace(wsId: string) {
  return apiPostRaw<{ left: boolean }>(`/api/workspaces/${encodeURIComponent(wsId)}/leave`);
}

export async function listWorkspaceInvitations(wsId: string) {
  return apiGetRaw<{ items: WorkspaceInvitation[] }>(
    `/api/workspaces/${encodeURIComponent(wsId)}/invitations`
  );
}

export async function revokeWorkspaceInvitation(wsId: string, token: string) {
  return apiDeleteRaw<{ revoked: boolean }>(
    `/api/workspaces/${encodeURIComponent(wsId)}/invitations/${encodeURIComponent(token)}`
  );
}

export async function verifyInvitation(token: string) {
  return apiGetRaw<{ workspaceName: string }>(
    `/api/invitations/${encodeURIComponent(token)}`
  );
}

export async function acceptInvitation(token: string) {
  return apiPostRaw<{ workspaceId: string; role: string; joined_at: string }>(
    `/api/invitations/${encodeURIComponent(token)}/accept`
  );
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

export async function putStorage(key: string, data: unknown): Promise<string | null> {
  try {
    const resp = await apiPut<{ key: string }>("/storage", { key, data });
    return resp.key || key;
  } catch {
    console.error("putStorage failed");
    return null;
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

export async function copySkillFilesFrom(agentId: string, skillId: string, sourceAgentId: string, sourceSkillId: string): Promise<number> {
  try {
    const resp = await apiPost<{ copied: number }>(
      `/agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}/copy-from`,
      { sourceAgentId, sourceSkillId }
    );
    return resp.copied;
  } catch (err) {
    console.error("copySkillFilesFrom failed:", err);
    return 0;
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
