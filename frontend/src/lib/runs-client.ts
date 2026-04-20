import { apiGet } from "./api-client";

// ── Runs (Sprint 2 F6) ──

export interface RunSummary {
  runId: string;
  trigger: "schedule" | "manual" | "chat" | "a2a";
  scheduleId?: string;
  status: "running" | "success" | "failure";
  input?: string;
  model?: string;
  totalTokens?: number;
  durationMs?: number;
  artifactCount?: number;
  startedAt: string;
  completedAt?: string;
}

export interface RunArtifact {
  key: string;
  filename: string;
}

export interface RunToolCall {
  name: string;
  input: unknown;
  output: unknown;
  status: string;
  durationMs?: number;
}

export interface RunOutput {
  text: string;
  toolCalls: RunToolCall[];
}

export interface RunDetail {
  runId: string;
  trigger: "schedule" | "manual" | "chat" | "a2a";
  scheduleId?: string;
  sessionId?: string;
  status: "running" | "success" | "failure";
  input?: string;
  outputUrl?: string;
  artifactRefs?: RunArtifact[];
  usage?: {
    inputTokens?: number;
    outputTokens?: number;
    totalTokens?: number;
  };
  durationMs?: number;
  model?: string;
  error?: string;
  startedAt: string;
  completedAt?: string;
}

export async function listRuns(agentId: string, limit = 50): Promise<RunSummary[]> {
  const resp = await apiGet<{ runs?: RunSummary[] }>(
    `/agents/${encodeURIComponent(agentId)}/runs?limit=${limit}`
  );
  return resp.runs ?? [];
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
