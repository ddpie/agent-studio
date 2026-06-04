import { apiGet } from "./api-client";

// ── Runs (Sprint 2 F6) ──

export interface RunSummary {
  runId: string;
  trigger: "schedule" | "manual";
  scheduleId: string | null;
  status: "running" | "completed" | "failed" | "timeout";
  input: string;
  model: string | null;
  totalTokens: number | null;
  durationMs: number | null;
  artifactCount: number;
  startedAt: string;
  completedAt: string | null;
}

export interface RunArtifact {
  key: string;
  filename: string;
}

export interface RunOutput {
  text: string;
  toolCalls: { name: string; input: string; output: string }[];
}

export interface RunDetail {
  runId: string;
  trigger: "schedule" | "manual";
  scheduleId: string | null;
  sessionId: string | null;
  status: "running" | "completed" | "failed" | "timeout";
  input: string;
  outputUrl: string | null;
  artifactRefs: RunArtifact[];
  usage: { promptTokens: number | null; completionTokens: number | null; totalTokens: number | null };
  durationMs: number | null;
  model: string | null;
  error: { code: string; message: string } | null;
  startedAt: string;
  completedAt: string | null;
}

export interface ListRunsOptions {
  limit?: number;
  scheduleId?: string;
  cursor?: string;
}

export async function listRuns(
  agentId: string,
  options: ListRunsOptions = {},
): Promise<{ runs: RunSummary[]; nextToken?: string }> {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit ?? 20));
  if (options.scheduleId) params.set("scheduleId", options.scheduleId);
  if (options.cursor) params.set("nextToken", options.cursor);
  return apiGet<{ runs: RunSummary[]; nextToken?: string }>(
    `/agents/${encodeURIComponent(agentId)}/runs?${params.toString()}`,
  );
}

export async function getRun(agentId: string, runId: string): Promise<RunDetail> {
  return apiGet<RunDetail>(
    `/agents/${encodeURIComponent(agentId)}/runs/${encodeURIComponent(runId)}`
  );
}

export async function fetchRunOutput(outputUrl: string): Promise<RunOutput> {
  const resp = await fetch(outputUrl);
  if (!resp.ok) throw new Error(`Failed to fetch run output: ${resp.status}`);
  return resp.json();
}
