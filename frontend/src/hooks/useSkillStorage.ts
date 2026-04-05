/**
 * Storage adapter hook for SkillDetail.
 * Returns file operation functions that target either global skill storage
 * or agent-private skill storage based on the provided params.
 */
import { useCallback, useMemo } from "react"
import {
  getSkillContent,
  getSkillFile,
  listSkillFiles,
  writeSkillFile,
  deleteSkillFile,
  renameSkillFile,
} from "../lib/skill-storage"
import {
  readAgentSkillFile,
  writeAgentSkillFile,
  listAgentSkillFiles,
} from "../lib/agent-skill-storage"
import { deleteFromS3 } from "../lib/s3-storage"

export interface SkillStorageOps {
  getContent: (path?: string) => Promise<string | null>
  getFile: (path: string) => Promise<string | null>
  listFiles: () => Promise<string[]>
  writeFile: (path: string, content: string) => Promise<boolean>
  deleteFile: (path: string) => Promise<boolean>
  renameFile: (oldPath: string, newPath: string) => Promise<boolean>
  isAgentMode: boolean
  /** The effective skill ID used for storage operations */
  storageSkillId: string
}

export function useSkillStorage(
  skillId: string,
  agentId?: string | null,
  agentSkillId?: string | null,
): SkillStorageOps {
  const isAgentMode = !!(agentId && agentSkillId)
  const storageSkillId = isAgentMode ? agentSkillId! : skillId

  const getContent = useCallback(async (path?: string) => {
    if (isAgentMode) {
      return readAgentSkillFile(agentId!, agentSkillId!, path || "SKILL.md")
    }
    return path ? getSkillFile(skillId, path) : getSkillContent(skillId)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  const getFile = useCallback(async (path: string) => {
    if (isAgentMode) {
      return readAgentSkillFile(agentId!, agentSkillId!, path)
    }
    return getSkillFile(skillId, path)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  const listFiles = useCallback(async () => {
    if (isAgentMode) {
      return listAgentSkillFiles(agentId!, agentSkillId!)
    }
    return listSkillFiles(skillId)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  const writeFile = useCallback(async (path: string, content: string) => {
    if (isAgentMode) {
      return writeAgentSkillFile(agentId!, agentSkillId!, path, content)
    }
    return writeSkillFile(skillId, path, content)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  const deleteFile = useCallback(async (path: string) => {
    if (isAgentMode) {
      const key = `agents/${agentId}/skills/${agentSkillId}/${path}`
      return deleteFromS3(key)
    }
    return deleteSkillFile(skillId, path)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  const renameFile = useCallback(async (oldPath: string, newPath: string) => {
    if (isAgentMode) {
      // Read old, write new, delete old
      const content = await readAgentSkillFile(agentId!, agentSkillId!, oldPath)
      if (content === null) return false
      const written = await writeAgentSkillFile(agentId!, agentSkillId!, newPath, content)
      if (!written) return false
      const key = `agents/${agentId}/skills/${agentSkillId}/${oldPath}`
      return deleteFromS3(key)
    }
    return renameSkillFile(skillId, oldPath, newPath)
  }, [skillId, agentId, agentSkillId, isAgentMode])

  return useMemo(() => ({
    getContent, getFile, listFiles, writeFile, deleteFile, renameFile,
    isAgentMode, storageSkillId,
  }), [getContent, getFile, listFiles, writeFile, deleteFile, renameFile, isAgentMode, storageSkillId])
}
