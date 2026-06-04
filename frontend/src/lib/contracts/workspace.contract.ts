import { z } from "zod";

/**
 * Zod contract schemas for Workspace-related API responses.
 * Mirrors WorkspaceDetail and WorkspaceMember in api-client.ts.
 */

export const WorkspaceMemberSchema = z.object({
  userId: z.string(),
  role: z.enum(["viewer", "editor", "admin", "owner"]),
  joined_at: z.string(),
  display_name: z.string().optional(),
  email: z.string().optional(),
});

export type WorkspaceMemberContract = z.infer<typeof WorkspaceMemberSchema>;

export const WorkspaceDetailSchema = z.object({
  workspaceId: z.string(),
  name: z.string(),
  description: z.string().optional(),
  owner_id: z.string().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
  memory_id: z.string().optional(),
  members: z.array(WorkspaceMemberSchema),
});

export type WorkspaceDetailContract = z.infer<typeof WorkspaceDetailSchema>;

export const WorkspaceListItemSchema = z.object({
  workspaceId: z.string(),
  name: z.string().optional(),
  description: z.string().optional(),
  role: z.string().optional(),
  created_at: z.string().optional(),
});

export type WorkspaceListItemContract = z.infer<typeof WorkspaceListItemSchema>;

export const WorkspaceListResponseSchema = z.object({
  items: z.array(WorkspaceListItemSchema),
});

export type WorkspaceListResponseContract = z.infer<typeof WorkspaceListResponseSchema>;
