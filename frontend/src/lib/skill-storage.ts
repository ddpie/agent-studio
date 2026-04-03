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
