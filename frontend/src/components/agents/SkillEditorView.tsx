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
import { useFileEditor } from "../../hooks/useFileEditor"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { computeSkillHash } from "../../lib/agent-skill-storage"
import { getFileIcon, type TreeNode } from "../../lib/tree-helpers"
import { getMonacoLanguage } from "../../lib/monaco-helpers"
import { validatePython } from "../../lib/validators/python-validator"
import { validateShell } from "../../lib/validators/shell-validator"
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
  const { updateSkillEntry, getPendingSkillFiles, clearPendingSkillFiles } = useAgentEditStore()

  const editor = useFileEditor({ storage })

  // Validation
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Resizable sidebar
  const [sidebarWidth, setSidebarWidth] = useState(200)
  const resizing = useRef(false)

  // Monaco ref for markers
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const monacoRef = useRef<Parameters<OnMount>[1] | null>(null)

  // Unsaved guard (Ctrl+S, beforeunload, route blocker)
  useUnsavedGuard({ hasChanges: editor.hasPendingOps, onSave: handleSave, saving: editor.saving })

  // --- Load all files on mount ---
  useEffect(() => {
    // Check if files are in memory (newly added, not yet saved to S3)
    const pendingFiles = getPendingSkillFiles(skill.id)
    if (pendingFiles && Object.keys(pendingFiles).length > 0) {
      editor.loadFromMemory(pendingFiles)
      return
    }
    // Otherwise load from S3
    editor.loadInitial().catch(() => {
      setError(t("agentSkills.failedToLoad"))
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, skill.id, skill.sourceSkillId])

  // --- Editor content for current file ---
  const currentContent = editor.content ?? ""

  // --- Switch file ---
  const switchFile = useCallback((path: string) => {
    if (editor.pendingDeletes.has(path)) return
    setValidationResult(null)
    editor.selectFile(path)
  }, [editor])

  // --- Editor onChange ---
  const handleEditorChange = useCallback((value: string | undefined) => {
    editor.handleEditorChange(value)
  }, [editor])

  // --- Python/Shell markers ---
  const handleEditorMount: OnMount = useCallback((ed, monaco) => {
    editorRef.current = ed
    monacoRef.current = monaco
  }, [])

  useEffect(() => {
    if (!monacoRef.current || !editorRef.current) return
    const monaco = monacoRef.current
    const model = editorRef.current.getModel()
    if (!model) return

    let markers: { startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number; message: string; severity: number }[] = []

    if (editor.currentFile.endsWith(".py")) {
      markers = validatePython(currentContent).map(m => ({
        startLineNumber: m.line, startColumn: m.col,
        endLineNumber: m.line, endColumn: m.col + 1,
        message: m.message, severity: monaco.MarkerSeverity.Error,
      }))
    } else if (editor.currentFile.endsWith(".sh") || editor.currentFile.endsWith(".bash")) {
      markers = validateShell(currentContent).map(m => ({
        startLineNumber: m.line, startColumn: m.col,
        endLineNumber: m.line, endColumn: m.col + 1,
        message: m.message, severity: monaco.MarkerSeverity.Error,
      }))
    }

    monaco.editor.setModelMarkers(model, "skill-editor", markers)
  }, [editor.currentFile, currentContent])

  // --- Batch save ---
  // eslint-disable-next-line react-hooks/exhaustive-deps
  async function handleSave() {
    const result = await editor.handleSaveAll()
    if (!result.success) {
      if (result.validationResult) setValidationResult(result.validationResult)
      else setError(result.errors.join(", "))
      return
    }

    // Clear pending files — they're now in S3
    clearPendingSkillFiles(skill.id)

    // Recompute hash and update agent-edit-store
    const allFiles: Record<string, string> = {}
    for (const f of editor.files) {
      if (!editor.pendingDeletes.has(f)) {
        allFiles[f] = editor.getEditedContent(f) ?? editor.getOriginalContent(f) ?? ""
      }
    }
    const newHash = await computeSkillHash(allFiles)
    updateSkillEntry(skill.id, { contentHash: newHash })

    setValidationResult(result.validationResult ?? { valid: true, errors: [], warnings: [] })
  }

  // --- New file ---
  const handleNewFile = useCallback(() => {
    const name = prompt(t("skillEditor.newFileName", "New file name (e.g. scripts/run.py):"))
    if (!name || !name.trim()) return
    const path = name.trim()
    if (!editor.stageNewFile(path)) {
      setError(t("skillEditor.fileExists", "File already exists"))
      return
    }
    editor.setCurrentFile(path)
    editor.setContent("")
  }, [editor, t])

  // --- Delete file ---
  const handleDeleteFile = useCallback((path: string) => {
    if (path === "SKILL.md") return
    if (!confirm(t("skillEditor.deleteConfirm", `Delete ${path}?`))) return
    editor.stageDelete(path)
  }, [editor, t])

  // --- Back with unsaved guard ---
  const handleBack = useCallback(() => {
    if (editor.hasPendingOps) {
      if (!confirm(t("skillEditor.unsavedChanges", "You have unsaved changes. Discard?"))) return
    }
    onBack()
  }, [editor.hasPendingOps, onBack, t])

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

  // --- Tree data from hook ---
  const treeData = editor.treeData

  // --- Tree node renderer ---
  function TreeNodeRow({ node, style }: NodeRendererProps<TreeNode>) {
    const isDir = !!node.data.children
    const isActive = !isDir && node.data.id === editor.currentFile
    const isDirty = editor.changedFiles.has(node.data.id) || editor.pendingCreates.has(node.data.id)
    const isDeleted = editor.pendingDeletes.has(node.data.id)

    if (isDeleted) return null

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

  if (editor.loadingContent && editor.files.length === 0) {
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
          disabled={!editor.hasPendingOps || editor.saving}
          className="flex items-center gap-1 px-3 py-1 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors"
        >
          {editor.saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
          {editor.hasPendingOps ? `${t("common.save", "Save")} (${editor.pendingCount})` : t("common.save", "Save")}
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
            language={getMonacoLanguage(editor.currentFile)}
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
