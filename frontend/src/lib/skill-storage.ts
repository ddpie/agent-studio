/**
 * Skill CRUD via S3 direct access (no Meta-Agent LLM).
 * Uses s3-storage.ts for signed S3 operations.
 */
import { readJsonFromS3, writeJsonToS3, deleteFromS3 } from "./s3-storage";
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../config";

const INDEX_KEY = "skills/index.json";

export interface SkillIndexEntry {
  id: string;
  name: string;
  description: string;
  deleted?: boolean;
  deletedAt?: number;
}

/** List all skills from index.json (excludes deleted) */
export async function listSkills(): Promise<SkillIndexEntry[]> {
  const index = await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY);
  return (index ?? []).filter(s => !s.deleted);
}

/** List deleted (trashed) skills */
export async function listDeletedSkills(): Promise<SkillIndexEntry[]> {
  const index = await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY);
  return (index ?? []).filter(s => s.deleted);
}

/** Read full SKILL.md content */
export async function getSkillContent(id: string): Promise<string | null> {
  return getSkillFile(id, "SKILL.md");
}

/** Read any file from a skill directory */
export async function getSkillFile(id: string, path: string): Promise<string | null> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return null;

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "s3",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(
      `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/skills/${id}/${path}`
    );
    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      headers: signed.headers as Record<string, string>,
    });
    if (!resp.ok) return null;
    return resp.text();
  } catch {
    return null;
  }
}

/** List all files in a skill directory (excluding SKILL.md) */
export async function listSkillFiles(id: string): Promise<string[]> {
  try {
    const { listS3Keys } = await import("./s3-storage");
    const prefix = `skills/${id}/`;
    const keys = await listS3Keys(prefix);
    return keys
      .map((k) => k.slice(prefix.length))
      .filter((f) => f && f !== "SKILL.md");
  } catch {
    return [];
  }
}

/** Write any file to a skill directory. If writing SKILL.md, syncs index.json from frontmatter. */
export async function writeSkillFile(id: string, path: string, content: string): Promise<boolean> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return false;

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "s3",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(
      `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/skills/${id}/${path}`
    );
    const body = new TextEncoder().encode(content);
    const contentType = path.endsWith(".py") ? "text/x-python"
      : path.endsWith(".json") ? "application/json"
      : path.endsWith(".md") ? "text/markdown"
      : "text/plain";

    const signed = await signer.sign({
      method: "PUT",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host, "Content-Type": contentType },
      body,
    });

    const resp = await fetch(url.toString(), {
      method: "PUT",
      headers: signed.headers as Record<string, string>,
      body,
    });
    if (!resp.ok) return false;

    // Sync index.json when SKILL.md is updated
    if (path === "SKILL.md") {
      const meta = parseFrontmatter(content);
      if (meta) {
        const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
        const entry = index.find((s) => s.id === id);
        if (entry) {
          entry.name = meta.name;
          entry.description = meta.description;
          await writeJsonToS3(INDEX_KEY, index);
        }
      }
    }

    return true;
  } catch {
    return false;
  }
}

/** Delete a single file from a skill directory */
export async function deleteSkillFile(id: string, path: string): Promise<boolean> {
  return deleteFromS3(`skills/${id}/${path}`);
}

/** Rename/move a file within a skill directory (copy + delete, S3 has no rename) */
export async function renameSkillFile(id: string, oldPath: string, newPath: string): Promise<boolean> {
  const content = await getSkillFile(id, oldPath);
  if (content === null) return false;
  const written = await writeSkillFile(id, newPath, content);
  if (!written) return false;
  return deleteFromS3(`skills/${id}/${oldPath}`);
}

/** Soft-delete a skill (move to trash) */
export async function deleteSkill(id: string): Promise<boolean> {
  const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
  const entry = index.find((s) => s.id === id);
  if (!entry) return false;
  entry.deleted = true;
  entry.deletedAt = Date.now();
  await writeJsonToS3(INDEX_KEY, index);
  return true;
}

/** Restore a soft-deleted skill */
export async function restoreSkill(id: string): Promise<boolean> {
  const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
  const entry = index.find((s) => s.id === id);
  if (!entry) return false;
  delete entry.deleted;
  delete entry.deletedAt;
  await writeJsonToS3(INDEX_KEY, index);
  return true;
}

/** Permanently delete a skill (remove files + index entry) */
export async function permanentlyDeleteSkill(id: string): Promise<boolean> {
  // Delete SKILL.md and all files
  try {
    const { listS3Keys } = await import("./s3-storage");
    const keys = await listS3Keys(`skills/${id}/`);
    for (const key of keys) {
      await deleteFromS3(key);
    }
  } catch {
    // If listing fails, at least delete SKILL.md
    await deleteFromS3(`skills/${id}/SKILL.md`);
  }

  const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
  const updated = index.filter((s) => s.id !== id);
  await writeJsonToS3(INDEX_KEY, updated);
  return true;
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
  const id = crypto.randomUUID().slice(0, 8);

  // Write SKILL.md
  const { fetchAuthSession } = await import("aws-amplify/auth");
  const { SignatureV4 } = await import("@smithy/signature-v4");
  const { Sha256 } = await import("@aws-crypto/sha256-js");
  const { agentConfig } = await import("../config");

  const { credentials } = await fetchAuthSession();
  if (!credentials) return null;

  const signer = new SignatureV4({
    service: "s3",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  });

  const url = new URL(
    `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/skills/${id}/SKILL.md`
  );
  const body = new TextEncoder().encode(skillMd);
  const signed = await signer.sign({
    method: "PUT",
    protocol: url.protocol,
    hostname: url.hostname,
    path: url.pathname,
    query: {},
    headers: { Host: url.host, "Content-Type": "text/markdown" },
    body,
  });

  const resp = await fetch(url.toString(), {
    method: "PUT",
    headers: signed.headers as Record<string, string>,
    body,
  });
  if (!resp.ok) return null;

  // Update index
  const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
  index.push({ id, name: skillName, description: skillDesc });
  await writeJsonToS3(INDEX_KEY, index);

  return { id, name: skillName, description: skillDesc };
}
