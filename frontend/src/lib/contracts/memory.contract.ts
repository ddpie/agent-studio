import { z } from "zod";

/**
 * Zod contract schemas for Memory-related API responses.
 * Mirrors MemoryRecord, MemorySection, MemoryBundle in api-client.ts.
 */

export const MemoryRecordSchema = z.object({
  id: z.string(),
  content: z.record(z.string(), z.unknown()),
  createdAt: z.number(),
  namespace: z.string(),
});

export type MemoryRecordContract = z.infer<typeof MemoryRecordSchema>;

export const MemorySectionSchema = z.object({
  records: z.array(MemoryRecordSchema),
  nextToken: z.string().nullable(),
});

export type MemorySectionContract = z.infer<typeof MemorySectionSchema>;

export const MemoryBundleSchema = z.object({
  preferences: MemorySectionSchema,
  facts: MemorySectionSchema,
  summaries: MemorySectionSchema,
  episodes: MemorySectionSchema,
});

export type MemoryBundleContract = z.infer<typeof MemoryBundleSchema>;
