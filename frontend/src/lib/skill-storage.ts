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
}

/** List all skills from index.json */
export async function listSkills(): Promise<SkillIndexEntry[]> {
  const index = await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY);
  return index ?? [];
}

/** Read full SKILL.md content */
export async function getSkillContent(id: string): Promise<string | null> {
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
      `https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}/skills/${id}/SKILL.md`
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

/** Delete a skill and update index */
export async function deleteSkill(id: string): Promise<boolean> {
  const deleted = await deleteFromS3(`skills/${id}/SKILL.md`);
  if (!deleted) return false;

  const index = (await readJsonFromS3<SkillIndexEntry[]>(INDEX_KEY)) ?? [];
  const updated = index.filter((s) => s.id !== id);
  await writeJsonToS3(INDEX_KEY, updated);
  return true;
}

/** Parse YAML frontmatter from SKILL.md content */
function parseFrontmatter(content: string): { name: string; description: string } | null {
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
function wrapWithFrontmatter(content: string, name: string, description: string): string {
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
