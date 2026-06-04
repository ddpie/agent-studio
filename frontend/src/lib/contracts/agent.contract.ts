import { z } from "zod";

/**
 * Zod contract schemas for Agent-related API responses.
 * These mirror the TypeScript interfaces in api-client.ts and serve
 * as runtime-checkable contracts for API integration tests.
 */

export const AgentListItemSchema = z.object({
  agentId: z.string(),
  name: z.string(),
  display_name: z.string().optional(),
  description: z.string().optional(),
  status: z.string(),
  visibility: z.string().optional(),
  model_id: z.string().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
  runtime_type: z.enum(["zip", "harness"]).optional(),
  harness_arn: z.string().optional(),
});

export type AgentListItemContract = z.infer<typeof AgentListItemSchema>;

export const PaginatedAgentsSchema = z.object({
  items: z.array(AgentListItemSchema),
  nextCursor: z.string().optional(),
});

export type PaginatedAgentsContract = z.infer<typeof PaginatedAgentsSchema>;

export const AgentRuntimeInfoSchema = z.object({
  status: z.enum([
    "CREATING",
    "CREATE_FAILED",
    "UPDATING",
    "UPDATE_FAILED",
    "READY",
    "DELETING",
  ]),
  lastUpdatedAt: z.string().optional(),
  description: z.string().optional(),
  agentRuntimeVersion: z.string().optional(),
  agentRuntimeName: z.string().optional(),
});

export type AgentRuntimeInfoContract = z.infer<typeof AgentRuntimeInfoSchema>;
