/**
 * SkillEditorView — Inline skill editor with file tree + Monaco editor.
 * Replaces SkillEditorModal for the agent edit page.
 */
import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslation } from "react-i18next"
import { ArrowLeft, Save, Loader2, Plus, Trash2 } from "lucide-react"
import MonacoEditor, { type OnMount } from "@monaco-editor/react"
import { Tree, type NodeRendererProps } from "react-arborist"
import useIsDark from "../../hooks/useIsDark"
import { useSkillStorage } from "../../hooks/useSkillStorage"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { computeSkillHash } from "../../lib/agent-skill-storage"
import { buildTreeData, getFileIcon, type TreeNode } from "../../lib/tree-helpers"
import { getMonacoLanguage } from "../../lib/monaco-helpers"
import { validatePython } from "../../lib/validators/python-validator"
import { validateShell } from "../../lib/validators/shell-validator"
import { validateSkill } from "../../lib/validators/skill-validator"
import ValidationBanner from "../shared/ValidationBanner"
import useUnsavedGuard from "../../hooks/useUnsavedGuard"
import type { AgentSkillEntry } from "../../lib/agent-metadata"
import type { ValidationResult } from "../../lib/types/validation"

interface SkillEditorViewProps {
  agentId: string
  skill: AgentSkillEntry
  onBack: () => void
}

export default function SkillEditorView({ agentId, skill, onBack }: SkillEditorViewProps) {
  const { t } = useTranslation()
  const isDark = useIsDark()
  const storage = useSkillStorage(skill.sourceSkillId, agentId, skill.id)
  const { updateSkillEntry } = useAgentEditStore()

  // File state
  const [files, setFiles] = useState<string[]>([])
  const [currentFile, setCurrentFile] = useState("SKILL.md")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Multi-file dirty tracking
  const [originalContents, setOriginalContents] = useState<Map<string, string>>(new Map())
  const [editedContents, setEditedContents] = useState<Map<string, string>>(new Map())
  const [changedFiles, setChangedFiles] = useState<Set<string>>(new Set())
  const [pendingCreates, setPendingCreates] = useState<Set<string>>(new Set())
  const [pendingDeletes, setPendingDeletes] = useState<Set<string>>(new Set())

  // Validation
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null)

  // Resizable sidebar
  const [sidebarWidth, setSidebarWidth] = useState(200)
  const resizing = useRef(false)

  // Monaco ref for markers
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const monacoRef = useRef<Parameters<OnMount>[1] | null>(null)

  const hasPendingOps = changedFiles.size > 0 || pendingCreates.size > 0 || pendingDeletes.size > 0
  const pendingCount = changedFiles.size + pendingCreates.size + pendingDeletes.size

  // Unsaved guard (Ctrl+S, beforeunload, route blocker)
  useUnsavedGuard({ hasChanges: hasPendingOps, onSave: handleSave, saving })

  // --- Load all files on mount ---
  useEffect(() => {
    setLoading(true)
    setError(null)
    Promise.all([storage.listFiles(), storage.getContent()])
      .then(async ([fileList, skillMd]) => {
        const allFiles = ["SKILL.md", ...fileList.filter((f: string) => f !== "SKILL.md")]
        const unique = [...new Set(allFiles)]
        setFiles(unique)

        const origMap = new Map<string, string>()
        const editMap = new Map<string, string>()
        origMap.set("SKILL.md", skillMd || "")
        editMap.set("SKILL.md", skillMd || "")

        // Load all other files
        for (const f of unique) {
          if (f === "SKILL.md") continue
          const content = await storage.getFile(f)
          origMap.set(f, content || "")
          editMap.set(f, content || "")
        }

        setOriginalContents(origMap)
        setEditedContents(editMap)
        setCurrentFile("SKILL.md")
        setChangedFiles(new Set())
        setPendingCreates(new Set())
        setPendingDeletes(new Set())
        setLoading(false)
      })
      .catch(() => {
        setError(t("agentSkills.failedToLoad"))
        setLoading(false)
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, skill.id, skill.sourceSkillId])

  // --- Editor content for current file ---
  const currentContent = editedContents.get(currentFile) ?? ""

  // --- Switch file ---
  const switchFile = useCallback((path: string) => {
    if (pendingDeletes.has(path)) return
    setCurrentFile(path)
    setValidationResult(null)
  }, [pendingDeletes])

  // --- Editor onChange ---
  const handleEditorChange = useCallback((value: string | undefined) => {
    const val = value ?? ""
    setEditedContents(prev => {
      const next = new Map(prev)
      next.set(currentFile, val)
      return next
    })
    const orig = originalContents.get(currentFile) ?? ""
    setChangedFiles(prev => {
      const next = new Set(prev)
      if (val !== orig) next.add(currentFile)
      else next.delete(currentFile)
      return next
    })
  }, [currentFile, originalContents])

  // --- Python/Shell markers ---
  const handleEditorMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
  }, [])

  useEffect(() => {
    if (!monacoRef.current || !editorRef.current) return
    const monaco = monacoRef.current
    const model = editorRef.current.getModel()
    if (!model) return

    let markers: { startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number; message: string; severity: number }[] = []

    if (currentFile.endsWith(".py")) {
      markers = validatePython(currentContent).map(m => ({
        startLineNumber: m.line, startColumn: m.col,
        endLineNumber: m.line, endColumn: m.col + 1,
        message: m.message, severity: monaco.MarkerSeverity.Error,
      }))
    } else if (currentFile.endsWith(".sh") || currentFile.endsWith(".bash")) {
      markers = validateShell(currentContent).map(m => ({
        startLineNumber: m.line, startColumn: m.col,
        endLineNumber: m.line, endColumn: m.col + 1,
        message: m.message, severity: monaco.MarkerSeverity.Error,
      }))
    }

    monaco.editor.setModelMarkers(model, "skill-editor", markers)
  }, [currentFile, currentContent])

  // --- Validate before save ---
  function runValidation(): ValidationResult {
    const skillMd = editedContents.get("SKILL.md") ?? ""
    const virtualFiles = files
      .filter(f => !pendingDeletes.has(f))
      .concat([...pendingCreates])
    return validateSkill(skillMd, virtualFiles, pendingDeletes)
  }

  // --- Batch save ---
  // eslint-disable-next-line react-hooks/exhaustive-deps
  async function handleSave() {
    const result = runValidation()
    if (!result.valid) {
      setValidationResult(result)
      return
    }

    setSaving(true)
    setError(null)
    try {
      // Delete files
      for (const path of pendingDeletes) {
        await storage.deleteFile(path)
      }
      // Write changed + created files
      const toWrite = new Set([...changedFiles, ...pendingCreates])
      for (const path of toWrite) {
        if (pendingDeletes.has(path)) continue
        const content = editedContents.get(path) ?? ""
        const ok = await storage.writeFile(path, content)
        if (!ok) throw new Error(`Failed to write ${path}`)
      }

      // Recompute hash
      const allFiles: Record<string, string> = {}
      for (const [path, content] of editedContents) {
        if (!pendingDeletes.has(path)) allFiles[path] = content
      }
      const newHash = await computeSkillHash(allFiles)
      updateSkillEntry(skill.id, { contentHash: newHash })

      // Update local state
      const newFileList = files.filter(f => !pendingDeletes.has(f))
      for (const f of pendingCreates) {
        if (!newFileList.includes(f)) newFileList.push(f)
      }
      setFiles(newFileList)

      const newOrig = new Map<string, string>()
      for (const f of newFileList) {
        newOrig.set(f, editedContents.get(f) ?? "")
      }
      setOriginalContents(newOrig)
      setChangedFiles(new Set())
      setPendingCreates(new Set())
      setPendingDeletes(new Set())

      // If current file was deleted, switch
      if (pendingDeletes.has(currentFile)) {
        setCurrentFile(newFileList[0] || "SKILL.md")
      }

      setValidationResult({ valid: true, errors: [], warnings: result.warnings })
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToSave", "Failed to save"))
    } finally {
      setSaving(false)
    }
  }

  // --- New file ---
  const handleNewFile = useCallback(() => {
    const name = prompt(t("skillEditor.newFileName", "New file name (e.g. scripts/run.py):"))
    if (!name || !name.trim()) return
    const path = name.trim()
    if (files.includes(path) || pendingCreates.has(path)) {
      setError(t("skillEditor.fileExists", "File already exists"))
      return
    }
    setPendingCreates(prev => new Set(prev).add(path))
    setEditedContents(prev => { const m = new Map(prev); m.set(path, ""); return m })
    setFiles(prev => [...prev, path])
    setCurrentFile(path)
  }, [files, pendingCreates, t])

  // --- Delete file ---
  const handleDeleteFile = useCallback((path: string) => {
    if (path === "SKILL.md") return // never delete SKILL.md
    if (!confirm(t("skillEditor.deleteConfirm", `Delete ${path}?`))) return

    if (pendingCreates.has(path)) {
      // Just remove from pending creates
      setPendingCreates(prev => { const s = new Set(prev); s.delete(path); return s })
      setFiles(prev => prev.filter(f => f !== path))
      setEditedContents(prev => { const m = new Map(prev); m.delete(path); return m })
    } else {
      setPendingDeletes(prev => new Set(prev).add(path))
    }
    setChangedFiles(prev => { const s = new Set(prev); s.delete(path); return s })
    if (currentFile === path) setCurrentFile("SKILL.md")
  }, [currentFile, pendingCreates, t])

  // --- Back with unsaved guard ---
  const handleBack = useCallback(() => {
    if (hasPendingOps) {
      if (!confirm(t("skillEditor.unsavedChanges", "You have unsaved changes. Discard?"))) return
    }
    onBack()
  }, [hasPendingOps, onBack, t])

  // --- Resize sidebar ---
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizing.current = true
    const startX = e.clientX
    const startW = sidebarWidth
    const onMove = (ev: MouseEvent) => {
      if (!resizing.current) return
      const newW = Math.max(120, Math.min(400, startW + ev.clientX - startX))
      setSidebarWidth(newW)
    }
    const onUp = () => { resizing.current = false; window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp) }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [sidebarWidth])

  // --- Tree data ---
  const visibleFiles = files.filter(f => !pendingDeletes.has(f))
  const treeData = buildTreeData(visibleFiles)

  // --- Tree node renderer ---
  function TreeNodeRow({ node, style }: NodeRendererProps<TreeNode>) {
    const isDir = !!node.data.children
    const isActive = !isDir && node.data.id === currentFile
    const isDirty = changedFiles.has(node.data.id) || pendingCreates.has(node.data.id)

    return (
      <div
        style={style}
        className={`flex items-center gap-1.5 px-2 py-0.5 cursor-pointer text-xs select-none group ${
          isActive
            ? isDark ? "bg-blue-900/30 text-blue-400" : "bg-blue-50 text-blue-600"
            : isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-600 hover:bg-gray-100"
        }`}
        onClick={() => {
          if (isDir) node.toggle()
          else switchFile(node.data.id)
        }}
      >
        {getFileIcon(node.data.name, isDir, node.isOpen)}
        <span className="truncate flex-1">{node.data.name}</span>
        {isDirty && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />}
        {!isDir && node.data.id !== "SKILL.md" && (
          <button
            onClick={(e) => { e.stopPropagation(); handleDeleteFile(node.data.id) }}
            className="hidden group-hover:block p-0.5 text-gray-400 hover:text-red-500"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className={`flex items-center gap-2 px-3 py-2 border-b ${isDark ? "border-gray-700 bg-gray-800" : "border-gray-200 bg-white"}`}>
        <button
          onClick={handleBack}
          className={`flex items-center gap-1 px-2 py-1 text-xs rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-200 hover:bg-gray-700" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          {t("common.back", "Back")}
        </button>
        <div className="w-px h-4 bg-gray-300 dark:bg-gray-600" />
        <span className={`text-sm font-semibold truncate ${isDark ? "text-gray-200" : "text-gray-800"}`}>
          {skill.name}
        </span>
        <span className="text-[10px] px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-full">
          Skill
        </span>
        <div className="flex-1" />
        {error && <span className="text-xs text-red-500 truncate max-w-[200px]">{error}</span>}
        <button
          onClick={handleNewFile}
          className={`flex items-center gap-1 px-2 py-1 text-xs rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-200 hover:bg-gray-700" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}
        >
          <Plus className="w-3.5 h-3.5" />
          {t("skillEditor.newFile", "New File")}
        </button>
        <button
          onClick={handleSave}
          disabled={!hasPendingOps || saving}
          className="flex items-center gap-1 px-3 py-1 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors"
        >
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          {hasPendingOps ? `${t("common.save", "Save")} (${pendingCount})` : t("common.save", "Save")}
        </button>
      </div>

      {/* Validation banner */}
      <ValidationBanner
        result={validationResult}
        onDismiss={() => setValidationResult(null)}
      />

      {/* Body: tree + editor */}
      <div className="flex flex-1 overflow-hidden">
        {/* File tree sidebar */}
        <div
          style={{ width: sidebarWidth }}
          className={`flex-shrink-0 border-r overflow-hidden flex flex-col ${isDark ? "border-gray-700 bg-gray-800/50" : "border-gray-200 bg-gray-50"}`}
        >
          <div className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider ${isDark ? "text-gray-500" : "text-gray-400"}`}>
            {t("skillEditor.files", "Files")}
          </div>
          <div className="flex-1 overflow-y-auto">
            <Tree<TreeNode>
              data={treeData}
              openByDefault
              width={sidebarWidth}
              rowHeight={26}
              indent={14}
              disableDrag
              disableDrop
            >
              {TreeNodeRow}
            </Tree>
          </div>
        </div>

        {/* Resize handle */}
        <div
          className="w-1 cursor-col-resize hover:bg-blue-400/30 active:bg-blue-400/50 flex-shrink-0"
          onMouseDown={handleResizeStart}
        />

        {/* Monaco editor */}
        <div className="flex-1 overflow-hidden">
          <MonacoEditor
            height="100%"
            value={currentContent}
            onChange={handleEditorChange}
            language={getMonacoLanguage(currentFile)}
            theme={isDark ? "vs-dark" : "light"}
            onMount={handleEditorMount}
            options={{
              fontSize: 13,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              wordWrap: "on",
              lineNumbers: "on",
              automaticLayout: true,
              padding: { top: 8 },
            }}
          />
        </div>
      </div>
    </div>
  )
}
