/**
 * Agent-private skill storage — CRUD for skills copied into agent's S3 space.
 * Path pattern: agents/{agentId}/skills/{skillId}/{filePath}
 * Uses SigV4 signing pattern from skill-storage.ts.
 */
import { fetchAuthSession } from "aws-amplify/auth"
import { agentConfig } from "../config"
import { listS3Keys, deleteFromS3 } from "./s3-storage"
import { getSkillFile, listSkillFiles as listGlobalSkillFiles, type SkillIndexEntry } from "./skill-storage"
import type { AgentSkillEntry } from "./agent-metadata"

const BUCKET = agentConfig.s3Bucket
const S3_ENDPOINT = `https://s3.${agentConfig.region}.amazonaws.com`

/** Validate file path to prevent traversal attacks */
function sanitizePath(path: string): string {
  const decoded = decodeURIComponent(path)
  const normalized = decoded
    .replace(/\\/g, "/")
    .replace(/\.\./g, "")
    .replace(/\/\//g, "/")
    .replace(/^\//, "")
  if (!normalized || normalized.startsWith("/")) return "invalid"
  return normalized
}

async function getSigner() {
  const { credentials } = await fetchAuthSession()
  if (!credentials) throw new Error("Not authenticated")

  const { SignatureV4 } = await import("@smithy/signature-v4")
  const { Sha256 } = await import("@aws-crypto/sha256-js")

  return new SignatureV4({
    service: "s3",
    region: agentConfig.region,
    credentials: {
      accessKeyId: credentials.accessKeyId,
      secretAccessKey: credentials.secretAccessKey,
      sessionToken: credentials.sessionToken,
    },
    sha256: Sha256,
  })
}

/**
 * Compute a deterministic content hash from file contents.
 * Sorts files by name, concatenates "filename:content", SHA-256, takes first 8 hex chars.
 */
export async function computeSkillHash(files: Record<string, string>): Promise<string> {
  const sorted = Object.keys(files).sort()
  const combined = sorted.map(f => `${f}:${files[f]}`).join("\n")
  const data = new TextEncoder().encode(combined)
  const hashBuffer = await crypto.subtle.digest("SHA-256", data)
  const hashArray = Array.from(new Uint8Array(hashBuffer))
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 8)
}

/**
 * Copy a global skill to agent's private space.
 * Reads all files from skills/{globalSkillId}/, writes to agents/{agentId}/skills/{newId}/.
 */
export async function copySkillToAgent(
  agentId: string,
  globalSkill: SkillIndexEntry,
  onProgress?: (done: number, total: number, phase: "read" | "write") => void,
): Promise<AgentSkillEntry> {
  const newId = crypto.randomUUID().slice(0, 8)

  // Phase 1: Read all files from global skill
  const [skillMd, extraFiles] = await Promise.all([
    getSkillFile(globalSkill.id, "SKILL.md"),
    listGlobalSkillFiles(globalSkill.id),
  ])

  const allFiles: Record<string, string> = {}
  if (skillMd) allFiles["SKILL.md"] = skillMd

  const readTotal = extraFiles.length
  let readDone = 0
  onProgress?.(0, readTotal, "read")

  // Read extra files with concurrency limit of 5
  for (let i = 0; i < extraFiles.length; i += 5) {
    const batch = extraFiles.slice(i, i + 5)
    const results = await Promise.all(
      batch.map(async f => ({ file: f, content: await getSkillFile(globalSkill.id, f) }))
    )
    for (const { file, content } of results) {
      if (content !== null) allFiles[file] = content
    }
    readDone += batch.length
    onProgress?.(readDone, readTotal, "read")
  }

  // Phase 2: Write all files with concurrency limit of 5
  const entries = Object.entries(allFiles)
  const writeTotal = entries.length
  let writeDone = 0
  onProgress?.(0, writeTotal, "write")

  const writeResults: { filePath: string; success: boolean }[] = []
  for (let i = 0; i < entries.length; i += 5) {
    const batch = entries.slice(i, i + 5)
    const batchResults = await Promise.all(
      batch.map(async ([filePath, content]) => ({
        filePath,
        success: await writeAgentSkillFile(agentId, newId, filePath, content),
      }))
    )
    writeResults.push(...batchResults)
    writeDone += batch.length
    onProgress?.(writeDone, writeTotal, "write")
  }

  const failed = writeResults.filter(r => !r.success)
  if (failed.length > 0) {
    const written = writeResults.filter(r => r.success).map(r => r.filePath)
    await Promise.all(written.map(f => deleteFromS3(`agents/${agentId}/skills/${newId}/${f}`)))
    throw new Error(`Failed to write: ${failed.map(r => r.filePath).join(", ")}`)
  }

  // Compute hash
  const contentHash = await computeSkillHash(allFiles)
  const sourceContentHash = globalSkill.contentHash || contentHash

  return {
    id: newId,
    sourceSkillId: globalSkill.id,
    sourceContentHash,
    name: globalSkill.name,
    description: globalSkill.description,
    contentHash,
    files: Object.keys(allFiles).sort(),
  }
}

/** Read a file from agent's private skill directory */
export async function readAgentSkillFile(
  agentId: string,
  skillId: string,
  filePath: string,
): Promise<string | null> {
  try {
    const safePath = sanitizePath(filePath)
    const signer = await getSigner()
    const key = `agents/${agentId}/skills/${skillId}/${safePath}`
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`)

    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host },
    })

    const resp = await fetch(url.toString(), {
      headers: signed.headers as Record<string, string>,
    })
    if (!resp.ok) return null
    return resp.text()
  } catch {
    return null
  }
}

/** Write a file to agent's private skill directory */
export async function writeAgentSkillFile(
  agentId: string,
  skillId: string,
  filePath: string,
  content: string,
): Promise<boolean> {
  try {
    const safePath = sanitizePath(filePath)
    const signer = await getSigner()
    const key = `agents/${agentId}/skills/${skillId}/${safePath}`
    const url = new URL(`${S3_ENDPOINT}/${BUCKET}/${key}`)
    const body = new TextEncoder().encode(content)

    const contentType = safePath.endsWith(".py") ? "text/x-python"
      : safePath.endsWith(".json") ? "application/json"
      : safePath.endsWith(".md") ? "text/markdown"
      : safePath.endsWith(".sh") ? "text/x-shellscript"
      : "text/plain"

    const signed = await signer.sign({
      method: "PUT",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { Host: url.host, "Content-Type": contentType },
      body,
    })

    const resp = await fetch(url.toString(), {
      method: "PUT",
      headers: signed.headers as Record<string, string>,
      body,
    })
    return resp.ok
  } catch {
    return false
  }
}

/** Delete all files for an agent's private skill */
export async function deleteAgentSkill(
  agentId: string,
  skillId: string,
): Promise<boolean> {
  try {
    const prefix = `agents/${agentId}/skills/${skillId}/`
    const keys = await listS3Keys(prefix)
    for (const key of keys) {
      await deleteFromS3(key)
    }
    return true
  } catch {
    return false
  }
}

/** List all files in an agent's private skill directory */
export async function listAgentSkillFiles(
  agentId: string,
  skillId: string,
): Promise<string[]> {
  try {
    const prefix = `agents/${agentId}/skills/${skillId}/`
    const keys = await listS3Keys(prefix)
    return keys
      .map(k => k.slice(prefix.length))
      .filter(f => f && !f.startsWith("."))
  } catch {
    return []
  }
}

/**
 * Read all files from a global skill without writing anywhere.
 * Returns { entry: AgentSkillEntry, files: Record<string, string> }
 */
export async function readGlobalSkillFiles(
  globalSkill: SkillIndexEntry,
): Promise<{ entry: AgentSkillEntry; files: Record<string, string> }> {
  const newId = crypto.randomUUID().slice(0, 8)

  const [skillMd, extraFileList] = await Promise.all([
    getSkillFile(globalSkill.id, "SKILL.md"),
    listGlobalSkillFiles(globalSkill.id),
  ])

  const allFiles: Record<string, string> = {}
  if (skillMd) allFiles["SKILL.md"] = skillMd

  // Read extra files with concurrency limit of 5
  for (let i = 0; i < extraFileList.length; i += 5) {
    const batch = extraFileList.slice(i, i + 5)
    const results = await Promise.all(
      batch.map(async f => ({ file: f, content: await getSkillFile(globalSkill.id, f) }))
    )
    for (const { file, content } of results) {
      if (content !== null) allFiles[file] = content
    }
  }

  const contentHash = await computeSkillHash(allFiles)
  const sourceContentHash = globalSkill.contentHash || contentHash

  const entry: AgentSkillEntry = {
    id: newId,
    sourceSkillId: globalSkill.id,
    sourceContentHash,
    name: globalSkill.name,
    description: globalSkill.description,
    contentHash,
    files: Object.keys(allFiles).sort(),
  }

  return { entry, files: allFiles }
}

/** Read all files for an agent skill and return as a map */
export async function readAllAgentSkillFiles(
  agentId: string,
  skillId: string,
): Promise<Record<string, string>> {
  const fileList = await listAgentSkillFiles(agentId, skillId)
  const result: Record<string, string> = {}
  for (const f of fileList) {
    const content = await readAgentSkillFile(agentId, skillId, f)
    if (content !== null) result[f] = content
  }
  return result
}
