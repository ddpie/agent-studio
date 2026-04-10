/**
 * useFileEditor — Reusable hook for multi-file editing with staging (create/delete/rename).
 * Extracted from SkillDetail.tsx to share between SkillDetail and SkillEditorView.
 */
import { useState, useMemo, useCallback, useRef } from "react"
import type { SkillStorageOps } from "./useSkillStorage"
import type { TreeNode } from "../lib/tree-helpers"
import type { ValidationResult } from "../lib/types/validation"
import { buildTreeData } from "../lib/tree-helpers"
import { validateSkill } from "../lib/validators/skill-validator"

export type { SkillStorageOps }

interface UseFileEditorParams {
  storage: SkillStorageOps
  onFileSwitch?: (path: string) => void
}

interface UseFileEditorReturn {
  // State
  files: string[]
  virtualFiles: string[]
  treeData: TreeNode[]
  currentFile: string
  content: string | null
  loadingContent: boolean
  saving: boolean
  hasPendingOps: boolean
  pendingCount: number
  changedFiles: Set<string>
  pendingDeletes: Set<string>
  pendingDeleteDirs: Set<string>
  pendingCreates: ReadonlyMap<string, string>
  pendingRenames: ReadonlyMap<string, string>

  // Actions
  loadInitial: (initialFile?: string) => Promise<{ files: string[]; content: string | null }>
  loadFromMemory: (files: Record<string, string>) => void
  selectFile: (path: string) => Promise<void>
  setCurrentFile: (path: string) => void
  setFiles: (files: string[]) => void
  setContent: (content: string | null) => void
  handleEditorChange: (val: string | undefined) => void
  handleSaveAll: () => Promise<{ success: boolean; errors: string[]; validationResult?: ValidationResult }>
  handleDiscard: () => void
  getDiffChanges: () => Map<string, { original: string; edited: string }>
  stageMove: (oldPath: string, newPath: string) => void
  stageNewFile: (path: string, content?: string) => boolean
  stageDelete: (path: string) => void
  stageDeleteDir: (dir: string) => void
  getOriginalContent: (path: string) => string | undefined
  getEditedContent: (path: string) => string | undefined
  setEditedContent: (path: string, content: string) => void
  markChanged: (path: string) => void
  markNewFromExternal: (path: string, content: string) => Promise<void>
  restoreEdits: (edits: Record<string, string>) => void
}

export function useFileEditor({ storage, onFileSwitch }: UseFileEditorParams): UseFileEditorReturn {
  // --- Core state ---
  const [files, setFiles] = useState<string[]>([])
  const [currentFile, setCurrentFileInternal] = useState("SKILL.md")
  const [content, setContent] = useState<string | null>(null)
  const [loadingContent, setLoadingContent] = useState(true)
  const [saving, setSaving] = useState(false)

  // Multi-file edit state (mutable maps for perf, trigger re-render via changedFiles)
  const [originalContents] = useState(() => new Map<string, string>())
  const [editedContents] = useState(() => new Map<string, string>())
  const [changedFiles, setChangedFiles] = useState<Set<string>>(new Set())

  // Staging state
  const [pendingCreates] = useState(() => new Map<string, string>())
  const [pendingDeletes, setPendingDeletes] = useState<Set<string>>(new Set())
  const [pendingDeleteDirs, setPendingDeleteDirs] = useState<Set<string>>(new Set())
  const [pendingRenames] = useState(() => new Map<string, string>())

  const loadingPathRef = useRef<string | null>(null)
  const currentFileRef = useRef(currentFile)

  const setCurrentFile = useCallback((path: string) => {
    currentFileRef.current = path
    setCurrentFileInternal(path)
  }, [])

  // --- Computed ---
  const virtualFiles = useMemo(() => {
    let vf = [...files]
    vf = vf.map(f => pendingRenames.get(f) ?? f)
    for (const path of pendingCreates.keys()) {
      // SKILL.md is always added by buildTreeData, don't duplicate
      if (path !== "SKILL.md" && !vf.includes(path)) vf.push(path)
    }
    return vf
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files, pendingDeletes, pendingRenames, pendingCreates, changedFiles])

  const treeData = useMemo(() => buildTreeData(virtualFiles), [virtualFiles])

  const hasPendingOps = changedFiles.size > 0 || pendingCreates.size > 0 || pendingDeletes.size > 0 || pendingDeleteDirs.size > 0 || pendingRenames.size > 0
  const pendingCount = changedFiles.size + pendingCreates.size + pendingDeletes.size + pendingDeleteDirs.size + pendingRenames.size

  // --- Actions ---

  const loadInitial = useCallback(async (initialFile?: string): Promise<{ files: string[]; content: string | null }> => {
    originalContents.clear()
    editedContents.clear()
    pendingCreates.clear()
    pendingDeletes.clear()
    pendingRenames.clear()
    setChangedFiles(new Set())
    setPendingDeletes(new Set())
    setPendingDeleteDirs(new Set())
    setLoadingContent(true)

    const [fileContent, fileList] = await Promise.all([
      initialFile ? storage.getFile(initialFile) : storage.getContent(),
      storage.listFiles(),
    ])

    setFiles(fileList)
    setContent(fileContent)
    if (fileContent !== null) {
      originalContents.set(initialFile || "SKILL.md", fileContent)
    }
    setCurrentFile(initialFile || "SKILL.md")
    setLoadingContent(false)

    return { files: fileList, content: fileContent }
  }, [storage, originalContents, editedContents, pendingCreates, pendingDeletes, pendingRenames, setCurrentFile])

  const loadFromMemory = useCallback((memFiles: Record<string, string>) => {
    originalContents.clear()
    editedContents.clear()
    pendingCreates.clear()
    pendingDeletes.clear()
    pendingRenames.clear()

    const fileList = Object.keys(memFiles).filter(f => f !== "SKILL.md").sort()
    setFiles(fileList)

    // Mark ALL files as pending creates — they need to be written to S3 on save
    for (const [path, content] of Object.entries(memFiles)) {
      pendingCreates.set(path, content)
    }

    setContent(memFiles["SKILL.md"] || null)
    setCurrentFile("SKILL.md")
    setLoadingContent(false)
    setChangedFiles(new Set())
    setPendingDeletes(new Set())
    setPendingDeleteDirs(new Set())
  }, [originalContents, editedContents, pendingCreates, pendingDeletes, pendingRenames, setCurrentFile])

  const selectFile = useCallback(async (path: string) => {
    if (pendingCreates.has(path)) {
      setContent(editedContents.get(path) ?? pendingCreates.get(path)!)
      setCurrentFile(path)
      return
    }
    if (editedContents.has(path)) {
      setContent(editedContents.get(path)!)
      setCurrentFile(path)
      return
    }
    loadingPathRef.current = path
    setLoadingContent(true)
    const fileContent = path === "SKILL.md"
      ? await storage.getContent()
      : await storage.getFile(path)
    if (loadingPathRef.current !== path) return
    setContent(fileContent)
    if (fileContent !== null) {
      originalContents.set(path, fileContent)
    }
    setCurrentFile(path)
    setLoadingContent(false)
  }, [editedContents, originalContents, pendingCreates, storage, setCurrentFile])

  const handleEditorChange = useCallback((val: string | undefined) => {
    if (val === undefined) return
    const path = currentFileRef.current
    editedContents.set(path, val)
    setContent(val)
    const original = originalContents.get(path)
    const next = new Set(changedFiles)
    if (original !== undefined && val !== original) {
      next.add(path)
    } else {
      next.delete(path)
    }
    setChangedFiles(next)
  }, [changedFiles, editedContents, originalContents])

  const stageMove = useCallback((oldPath: string, newPath: string) => {
    if (newPath === oldPath) return

    if (pendingCreates.has(oldPath)) {
      const c = editedContents.get(oldPath) ?? pendingCreates.get(oldPath) ?? ""
      pendingCreates.delete(oldPath)
      pendingCreates.set(newPath, c)
      if (editedContents.has(oldPath)) {
        editedContents.set(newPath, editedContents.get(oldPath)!)
        editedContents.delete(oldPath)
      }
    } else {
      let originalKey: string | null = null
      for (const [k, v] of pendingRenames) {
        if (v === oldPath) { originalKey = k; break }
      }
      if (originalKey !== null) {
        if (newPath === originalKey) {
          pendingRenames.delete(originalKey)
        } else {
          pendingRenames.set(originalKey, newPath)
        }
      } else {
        pendingRenames.set(oldPath, newPath)
      }
      if (editedContents.has(oldPath)) {
        editedContents.set(newPath, editedContents.get(oldPath)!)
        editedContents.delete(oldPath)
      }
      if (originalContents.has(oldPath)) {
        originalContents.set(newPath, originalContents.get(oldPath)!)
        originalContents.delete(oldPath)
      }
    }

    const next = new Set(changedFiles)
    next.delete(oldPath)
    next.add(newPath)
    setChangedFiles(next)

    if (oldPath === currentFileRef.current) {
      setCurrentFile(newPath)
      const c = editedContents.get(newPath) ?? originalContents.get(newPath) ?? ""
      setContent(c)
      onFileSwitch?.(newPath)
    }
  }, [changedFiles, editedContents, originalContents, pendingCreates, pendingRenames, onFileSwitch, setCurrentFile])

  const stageNewFile = useCallback((path: string, fileContent?: string): boolean => {
    if (virtualFiles.includes(path) || pendingCreates.has(path)) return false
    pendingCreates.set(path, fileContent ?? "")
    editedContents.set(path, fileContent ?? "")
    setChangedFiles(new Set([...changedFiles, path]))
    return true
  }, [virtualFiles, pendingCreates, editedContents, changedFiles])

  const stageDelete = useCallback((path: string) => {
    const next = new Set(changedFiles)
    const nextDeletes = new Set(pendingDeletes)

    if (pendingCreates.has(path)) {
      pendingCreates.delete(path)
      editedContents.delete(path)
      next.delete(path)
    } else {
      nextDeletes.add(path)
      editedContents.delete(path)
      originalContents.delete(path)
      next.delete(path)
    }

    setChangedFiles(next)
    setPendingDeletes(nextDeletes)

    if (path === currentFileRef.current) {
      setCurrentFile("SKILL.md")
      const orig = originalContents.get("SKILL.md")
      if (orig !== undefined) setContent(orig)
      onFileSwitch?.("SKILL.md")
    }
  }, [changedFiles, pendingDeletes, pendingCreates, editedContents, originalContents, onFileSwitch, setCurrentFile])

  const stageDeleteDir = useCallback((dir: string) => {
    const nextDirs = new Set(pendingDeleteDirs)
    nextDirs.add(dir)
    setPendingDeleteDirs(nextDirs)

    const next = new Set(changedFiles)
    for (const f of [...files, ...pendingCreates.keys()]) {
      if (f.startsWith(dir + "/")) {
        editedContents.delete(f)
        next.delete(f)
      }
    }
    setChangedFiles(next)

    if (currentFileRef.current.startsWith(dir + "/")) {
      setCurrentFile("SKILL.md")
      const orig = originalContents.get("SKILL.md")
      if (orig !== undefined) setContent(orig)
      onFileSwitch?.("SKILL.md")
    }
  }, [pendingDeleteDirs, changedFiles, files, pendingCreates, editedContents, originalContents, onFileSwitch, setCurrentFile])

  const handleSaveAll = useCallback(async (): Promise<{ success: boolean; errors: string[]; validationResult?: ValidationResult }> => {
    if (!hasPendingOps) return { success: true, errors: [] }

    // Validate SKILL.md
    const skillMd = editedContents.get("SKILL.md") ?? originalContents.get("SKILL.md") ?? ""
    const valResult = validateSkill(skillMd, virtualFiles, pendingDeletes)
    if (!valResult.valid) return { success: false, errors: valResult.errors, validationResult: valResult }

    setSaving(true)
    const errors: string[] = []

    try {
      // 1. Execute deletes
      for (const path of pendingDeletes) {
        try { await storage.deleteFile(path) }
        catch { errors.push(`Failed to delete ${path}`) }
      }

      // 1b. Execute directory deletes
      for (const dir of pendingDeleteDirs) {
        const dirFiles = files.filter(f => f.startsWith(dir + "/"))
        for (const f of dirFiles) {
          try { await storage.deleteFile(f) }
          catch { errors.push(`Failed to delete ${f}`) }
        }
      }

      // 2. Execute renames
      for (const [oldPath, newPath] of pendingRenames) {
        if (!pendingDeletes.has(oldPath)) {
          try { await storage.renameFile(oldPath, newPath) }
          catch { errors.push(`Failed to rename ${oldPath} → ${newPath}`) }
        }
      }

      // 3. Save created files
      for (const [path, c] of pendingCreates) {
        const edited = editedContents.get(path) ?? c
        try { await storage.writeFile(path, edited) }
        catch { errors.push(`Failed to create ${path}`) }
      }

      // 4. Save edited existing files
      for (const path of changedFiles) {
        if (pendingCreates.has(path) || pendingDeletes.has(path)) continue
        const c = editedContents.get(path)
        if (c === undefined) continue
        const actualPath = pendingRenames.get(path) ?? path
        try { await storage.writeFile(actualPath, c) }
        catch { errors.push(`Failed to save ${actualPath}`) }
      }

      if (errors.length > 0) {
        return { success: false, errors, validationResult: { valid: false, errors, warnings: [] } }
      }

      // Success — clear staging state
      pendingCreates.clear()
      pendingDeletes.clear()
      pendingDeleteDirs.clear()
      pendingRenames.clear()
      editedContents.clear()
      originalContents.clear()
      setChangedFiles(new Set())
      setPendingDeletes(new Set())
      setPendingDeleteDirs(new Set())

      // Refresh from storage
      const [newFiles, newContent] = await Promise.all([
        storage.listFiles(),
        storage.getContent(),
      ])
      setFiles(newFiles)
      if (newContent !== null) {
        originalContents.set("SKILL.md", newContent)
        if (currentFileRef.current === "SKILL.md") setContent(newContent)
      }
      if (currentFileRef.current !== "SKILL.md") {
        const fc = await storage.getFile(currentFileRef.current)
        if (fc !== null) {
          setContent(fc)
          originalContents.set(currentFileRef.current, fc)
        }
      }

      return { success: true, errors: [], validationResult: valResult }
    } finally {
      setSaving(false)
    }
  }, [hasPendingOps, virtualFiles, files, changedFiles, pendingDeletes, pendingDeleteDirs, pendingCreates, pendingRenames, editedContents, originalContents, storage])

  const handleDiscard = useCallback(() => {
    pendingCreates.clear()
    pendingDeletes.clear()
    pendingDeleteDirs.clear()
    pendingRenames.clear()
    editedContents.clear()
    setChangedFiles(new Set())
    setPendingDeletes(new Set())
    setPendingDeleteDirs(new Set())
    const orig = originalContents.get(currentFileRef.current)
    if (orig !== undefined) setContent(orig)
  }, [pendingCreates, pendingDeletes, pendingDeleteDirs, pendingRenames, editedContents, originalContents])

  const getDiffChanges = useCallback((): Map<string, { original: string; edited: string }> => {
    const result = new Map<string, { original: string; edited: string }>()
    for (const path of changedFiles) {
      if (pendingDeletes.has(path)) continue
      const original = originalContents.get(path) ?? ""
      const edited = editedContents.get(path) ?? ""
      result.set(path, { original, edited })
    }
    for (const path of pendingDeletes) {
      const original = originalContents.get(path) ?? ""
      result.set(`${path} (deleted)`, { original, edited: "" })
    }
    for (const [path, c] of pendingCreates) {
      const edited = editedContents.get(path) ?? c
      result.set(`${path} (new)`, { original: "", edited })
    }
    for (const [oldPath, newPath] of pendingRenames) {
      if (!result.has(newPath) && !pendingDeletes.has(oldPath)) {
        result.set(`${oldPath} → ${newPath}`, {
          original: originalContents.get(newPath) ?? "",
          edited: editedContents.get(newPath) ?? originalContents.get(newPath) ?? "",
        })
      }
    }
    return result
  }, [changedFiles, pendingDeletes, pendingCreates, pendingRenames, originalContents, editedContents])

  const getOriginalContent = useCallback((path: string) => originalContents.get(path), [originalContents])
  const getEditedContent = useCallback((path: string) => editedContents.get(path), [editedContents])

  const setEditedContent = useCallback((path: string, c: string) => {
    editedContents.set(path, c)
  }, [editedContents])

  const markChanged = useCallback((path: string) => {
    const orig = originalContents.get(path)
    const edited = editedContents.get(path)
    const next = new Set(changedFiles)
    if (orig !== undefined && edited !== undefined && edited !== orig) {
      next.add(path)
    } else if (orig === undefined && edited !== undefined) {
      next.add(path)
    }
    setChangedFiles(next)
  }, [changedFiles, originalContents, editedContents])

  const markNewFromExternal = useCallback(async (path: string, c: string) => {
    // If file exists but original not loaded yet, load it first
    if (!originalContents.has(path) && files.includes(path)) {
      try {
        const origContent = await storage.getFile(path);
        if (origContent !== null) {
          originalContents.set(path, origContent);
        }
      } catch { /* ignore */ }
    }

    editedContents.set(path, c)
    const orig = originalContents.get(path)
    const next = new Set(changedFiles)
    if (orig !== undefined && c !== orig) {
      next.add(path)
    } else if (orig === undefined) {
      if (!pendingCreates.has(path)) pendingCreates.set(path, "")
      next.add(path)
    }
    setChangedFiles(next)
    if (path === currentFileRef.current) setContent(c)
  }, [changedFiles, editedContents, originalContents, pendingCreates, files, storage])

  const restoreEdits = useCallback((edits: Record<string, string>) => {
    const next = new Set(changedFiles)
    for (const [path, c] of Object.entries(edits)) {
      editedContents.set(path, c)
      const orig = originalContents.get(path)
      if (orig !== undefined && c !== orig) {
        next.add(path)
      } else if (orig === undefined) {
        next.add(path)
      }
    }
    setChangedFiles(next)
    // Update current file content if it was edited
    const currentEdited = edits[currentFileRef.current]
    if (currentEdited !== undefined) setContent(currentEdited)
  }, [changedFiles, editedContents, originalContents])

  return {
    files,
    virtualFiles,
    treeData,
    currentFile,
    content,
    loadingContent,
    saving,
    hasPendingOps,
    pendingCount,
    changedFiles,
    pendingDeletes,
    pendingDeleteDirs,
    pendingCreates,
    pendingRenames,

    loadInitial,
    loadFromMemory,
    selectFile,
    setCurrentFile,
    setFiles,
    setContent,
    handleEditorChange,
    handleSaveAll,
    handleDiscard,
    getDiffChanges,
    stageMove,
    stageNewFile,
    stageDelete,
    stageDeleteDir,
    getOriginalContent,
    getEditedContent,
    setEditedContent,
    markChanged,
    markNewFromExternal,
    restoreEdits,
  }
}
