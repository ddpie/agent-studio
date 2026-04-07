/**
 * Skill CRUD via Lambda API (migrated from S3 direct access).
 * Pure functions (parseFrontmatter, wrapWithFrontmatter, computeContentHash) are preserved.
 */
import {
  fetchSkills,
  fetchDeletedSkills,
  fetchSkillFile,
  fetchSkillFiles,
  putSkillFile,
  deleteSkillFileApi,
  deleteSkillApi,
  restoreSkillApi,
  permanentlyDeleteSkillApi,
  importSkillApi,
} from "./api-client";

/**
 * Compute a deterministic content hash from file contents.
 * Sorts files by name, concatenates "filename:content", SHA-256, takes first 8 hex chars.
 */
export async function computeContentHash(files: Record<string, string>): Promise<string> {
  const sorted = Object.keys(files).sort()
  const combined = sorted.map(f => `${f}:${files[f]}`).join("\n")
  const data = new TextEncoder().encode(combined)
  const hashBuffer = await crypto.subtle.digest("SHA-256", data)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 8)
}

export interface SkillIndexEntry {
  id: string;
  name: string;
  description: string;
  contentHash?: string;    // not stored in DDB — recompute after reading files
  files?: string[];        // not stored in DDB — recompute after reading files
  deleted?: boolean;
  deletedAt?: number;
}

function mapSkillItem(item: Record<string, any>): SkillIndexEntry {
  return {
    id: item.skillId || "",
    name: item.name || "",
    description: item.description || "",
    contentHash: "",
    files: [],
    deleted: item.deleted || false,
    deletedAt: item.deleted_at ? new Date(item.deleted_at).getTime() : undefined,
  };
}

/** List all skills (excludes deleted) */
export async function listSkills(): Promise<SkillIndexEntry[]> {
  try {
    const resp = await fetchSkills(undefined, 100);
    return (resp.items || []).map(mapSkillItem);
  } catch {
    return [];
  }
}

/** List deleted (trashed) skills */
export async function listDeletedSkills(): Promise<SkillIndexEntry[]> {
  try {
    const resp = await fetchDeletedSkills(undefined, 100);
    return (resp.items || []).map(mapSkillItem);
  } catch {
    return [];
  }
}

/** Read full SKILL.md content */
export async function getSkillContent(id: string): Promise<string | null> {
  return fetchSkillFile(id, "SKILL.md");
}

/** Read any file from a skill directory */
export async function getSkillFile(id: string, path: string): Promise<string | null> {
  return fetchSkillFile(id, path);
}

/** List all files in a skill directory (excluding SKILL.md) */
export async function listSkillFiles(id: string): Promise<string[]> {
  try {
    const files = await fetchSkillFiles(id);
    return files.filter((f) => f && f !== "SKILL.md" && f !== "assistant-history.json" && !f.startsWith("."));
  } catch {
    return [];
  }
}

/** Write any file to a skill directory. If writing SKILL.md, the Lambda syncs frontmatter to DDB. */
export async function writeSkillFile(id: string, path: string, content: string): Promise<boolean> {
  return putSkillFile(id, path, content);
}

/** Delete a single file from a skill directory */
export async function deleteSkillFile(id: string, path: string): Promise<boolean> {
  return deleteSkillFileApi(id, path);
}

/** Rename/move a file within a skill directory (read + write + delete, S3 has no rename) */
export async function renameSkillFile(id: string, oldPath: string, newPath: string): Promise<boolean> {
  const content = await fetchSkillFile(id, oldPath);
  if (content === null) return false;
  const written = await putSkillFile(id, newPath, content);
  if (!written) return false;
  return deleteSkillFileApi(id, oldPath);
}

/** Soft-delete a skill (move to trash) */
export async function deleteSkill(id: string): Promise<boolean> {
  return deleteSkillApi(id);
}

/** Restore a soft-deleted skill */
export async function restoreSkill(id: string): Promise<boolean> {
  return restoreSkillApi(id);
}

/** Permanently delete a skill (remove files + DDB record) */
export async function permanentlyDeleteSkill(id: string): Promise<boolean> {
  return permanentlyDeleteSkillApi(id);
}

/** Parse YAML frontmatter from SKILL.md content */
export function parseFrontmatter(content: string): { name: string; description: string } | null {
  if (!content.startsWith("---")) return null;
  const parts = content.split("---", 3);
  if (parts.length < 3) return null;
  let name = "", description = "";
  for (const line of parts[1].trim().split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("name:")) name = trimmed.split(":", 2)[1].trim().replace(/^["']|["']$/g, "");
    if (trimmed.startsWith("description:")) description = trimmed.split(":", 2)[1].trim().replace(/^["']|["']$/g, "");
  }
  return name ? { name, description } : null;
}

/** Wrap plain markdown with AgentSkills.io frontmatter */
export function wrapWithFrontmatter(content: string, name: string, description: string): string {
  return `---
name: "${name}"
description: "${description}"
type: "prompt"
source: "imported"
user-invocable: true
---

${content.trim()}
`;
}

/** Import a skill from raw content (SKILL.md or plain markdown) */
export async function importSkill(
  content: string,
  name?: string,
  description?: string,
): Promise<{ id: string; name: string; description: string } | null> {
  const meta = parseFrontmatter(content);
  const skillName = meta?.name ?? name;
  const skillDesc = meta?.description ?? description ?? "";

  if (!skillName) return null; // need a name

  const skillMd = meta ? content : wrapWithFrontmatter(content, skillName, skillDesc);

  const resp = await importSkillApi({ content: skillMd, name: skillName, description: skillDesc });
  if (!resp) return null;

  return {
    id: resp.skillId || "",
    name: resp.name || skillName,
    description: resp.description || skillDesc,
  };
}

/**
 * Import a multi-file skill. Writes all files via Lambda import endpoint.
 * files: Record<path, content> — must include "SKILL.md".
 */
export async function importSkillFromFiles(
  files: Record<string, string>,
  onWriteProgress?: (current: number, total: number) => void,
): Promise<{ id: string; name: string; description: string } | null> {
  const skillMd = files["SKILL.md"];
  if (!skillMd) return null;

  const meta = parseFrontmatter(skillMd);
  const skillName = meta?.name ?? "imported-skill";
  const skillDesc = meta?.description ?? "";

  onWriteProgress?.(0, 1);

  // Separate scripts (scripts/*) from other extra files
  const scripts: Record<string, string> = {};
  const extraFiles: Record<string, string> = {};
  for (const [path, content] of Object.entries(files)) {
    if (path === "SKILL.md") continue;
    if (path.startsWith("scripts/")) {
      scripts[path.slice("scripts/".length)] = content;
    } else {
      extraFiles[path] = content;
    }
  }

  const resp = await importSkillApi({
    content: skillMd,
    name: skillName,
    description: skillDesc,
    scripts,
    files: extraFiles,
  });

  onWriteProgress?.(1, 1);

  if (!resp) return null;

  return {
    id: resp.skillId || "",
    name: resp.name || skillName,
    description: resp.description || skillDesc,
  };
}
