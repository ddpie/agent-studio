import { z } from "zod";

/**
 * Zod contract schemas for Run-related API responses.
 * Mirrors RunSummary and RunDetail in runs-client.ts.
 */

export const RunSummarySchema = z.object({
  runId: z.string(),
  trigger: z.enum(["schedule", "manual"]),
  scheduleId: z.string().nullable(),
  status: z.enum(["running", "completed", "failed", "timeout"]),
  input: z.string(),
  model: z.string().nullable(),
  totalTokens: z.number().nullable(),
  durationMs: z.number().nullable(),
  artifactCount: z.number(),
  startedAt: z.string(),
  completedAt: z.string().nullable(),
});

export type RunSummaryContract = z.infer<typeof RunSummarySchema>;

export const RunArtifactSchema = z.object({
  key: z.string(),
  filename: z.string(),
});

export const RunDetailSchema = z.object({
  runId: z.string(),
  trigger: z.enum(["schedule", "manual"]),
  scheduleId: z.string().nullable(),
  sessionId: z.string().nullable(),
  status: z.enum(["running", "completed", "failed", "timeout"]),
  input: z.string(),
  outputUrl: z.string().nullable(),
  artifactRefs: z.array(RunArtifactSchema),
  usage: z.object({
    promptTokens: z.number().nullable(),
    completionTokens: z.number().nullable(),
    totalTokens: z.number().nullable(),
  }),
  durationMs: z.number().nullable(),
  model: z.string().nullable(),
  error: z
    .object({
      code: z.string(),
      message: z.string(),
    })
    .nullable(),
  startedAt: z.string(),
  completedAt: z.string().nullable(),
});

export type RunDetailContract = z.infer<typeof RunDetailSchema>;

export const ListRunsResponseSchema = z.object({
  runs: z.array(RunSummarySchema),
  nextToken: z.string().optional(),
});

export type ListRunsResponseContract = z.infer<typeof ListRunsResponseSchema>;
