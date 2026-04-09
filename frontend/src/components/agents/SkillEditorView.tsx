/**
 * SkillEditorView — Inline skill editor with file tree + Monaco editor.
 * All edits stay in memory. No standalone save — saves with agent deploy.
 */
import { useState, useEffect, useCallback, useRef } from "react"
import { useTranslation } from "react-i18next"
import { ArrowLeft, Loader2, FolderPlus, Trash2, Plus } from "lucide-react"
import MonacoEditor, { type OnMount } from "@monaco-editor/react"
import { Tree, type NodeRendererProps } from "react-arborist"
import useIsDark from "../../hooks/useIsDark"
import { useSkillStorage } from "../../hooks/useSkillStorage"
import { useFileEditor } from "../../hooks/useFileEditor"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { getFileIcon, type TreeNode } from "../../lib/tree-helpers"
import { getMonacoLanguage } from "../../lib/monaco-helpers"
import { validatePython } from "../../lib/validators/python-validator"
import { validateShell } from "../../lib/validators/shell-validator"
import type { AgentSkillEntry } from "../../lib/agent-metadata"

interface SkillEditorViewProps {
  agentId: string
  skill: AgentSkillEntry
  onBack: () => void
}

export default function SkillEditorView({ agentId, skill, onBack }: SkillEditorViewProps) {
  const { t } = useTranslation()
  const isDark = useIsDark()
  const storage = useSkillStorage(skill.sourceSkillId, agentId, skill.id)
  const { getPendingSkillFiles, updatePendingSkillFile, initSkillFiles } = useAgentEditStore()

  const editor = useFileEditor({ storage })

  const [error, setError] = useState<string | null>(null)
  const [confirmDeletePath, setConfirmDeletePath] = useState<string | null>(null)

  // Resizable sidebar
  const [sidebarWidth, setSidebarWidth] = useState(200)
  const resizing = useRef(false)
  const treeContainerRef = useRef<HTMLDivElement>(null)
  const [treeHeight, setTreeHeight] = useState(400)

  // Monaco ref for markers
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null)
  const monacoRef = useRef<Parameters<OnMount>[1] | null>(null)

  // Measure tree container height
  useEffect(() => {
    const el = treeContainerRef.current
    if (!el) return
    const obs = new ResizeObserver(([entry]) => {
      setTreeHeight(entry.contentRect.height)
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // --- Load all files on mount ---
  useEffect(() => {
    const loadEverything = async () => {
      try {
        // Check if we already have pending files in store (e.g. just added skill)
        const pendingFiles = getPendingSkillFiles(skill.id)
        if (pendingFiles && Object.keys(pendingFiles).length > 0) {
          editor.loadFromMemory(pendingFiles)
          console.log(`[SkillEditor] loaded ${Object.keys(pendingFiles).length} files from store`)
          return
        }

        const { content, files } = await editor.loadInitial()
        console.log(`[SkillEditor] loadInitial: ${files?.length} files, content=${content ? content.length : null} chars`)
        // Set original baseline for diff (only SKILL.md to start)
        const baseFiles: Record<string, string> = {}
        if (content !== null) baseFiles["SKILL.md"] = content
        initSkillFiles(skill.id, baseFiles)
      } catch (err) {
        console.error("[SkillEditor] load failed:", err)
        setError(t("agentSkills.failedToLoad"))
      }
    }
    loadEverything()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId, skill.id, skill.sourceSkillId])

  const currentContent = editor.content ?? ""

  // --- Switch file — also capture original for diff ---
  const switchFile = useCallback((path: string) => {
    if (editor.pendingDeletes.has(path)) return
    editor.selectFile(path).then(() => {
      // Capture original content for diff if not already tracked
      const { originalSkillFiles } = useAgentEditStore.getState()
      const orig = originalSkillFiles[skill.id] || {}
      if (!(path in orig)) {
        const originalContent = editor.getOriginalContent(path)
        if (originalContent !== undefined) {
          useAgentEditStore.setState({
            originalSkillFiles: {
              ...useAgentEditStore.getState().originalSkillFiles,
              [skill.id]: { ...useAgentEditStore.getState().originalSkillFiles[skill.id], [path]: originalContent },
            },
            pendingSkillFiles: {
              ...useAgentEditStore.getState().pendingSkillFiles,
              [skill.id]: { ...useAgentEditStore.getState().pendingSkillFiles[skill.id], [path]: originalContent },
            },
          })
        }
      }
    })
  }, [editor, skill.id])

  // --- Editor onChange — sync to hook + store ---
  const handleEditorChange = useCallback((value: string | undefined) => {
    editor.handleEditorChange(value)
    // Sync to store so diff can see changes
    if (value !== undefined) {
      updatePendingSkillFile(skill.id, editor.currentFile, value)
    }
  }, [editor, skill.id, updatePendingSkillFile])

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

  // --- Delete file ---
  const handleDeleteFile = useCallback((path: string) => {
    if (path === "SKILL.md") return
    setConfirmDeletePath(path)
  }, [])

  const confirmDeleteFile = useCallback(() => {
    if (confirmDeletePath) {
      editor.stageDelete(confirmDeletePath)
      setConfirmDeletePath(null)
    }
  }, [editor, confirmDeletePath])

  // --- Resize sidebar ---
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    resizing.current = true
    const startX = e.clientX
    const startW = sidebarWidth
    const onMove = (ev: MouseEvent) => {
      if (!resizing.current) return
      setSidebarWidth(Math.max(120, Math.min(400, startW + ev.clientX - startX)))
    }
    const onUp = () => { resizing.current = false; window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp) }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
  }, [sidebarWidth])

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
          onClick={onBack}
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
      </div>

      {/* Body: tree + editor */}
      <div className="flex flex-1 overflow-hidden">
        {/* File tree sidebar */}
        <div
          style={{ width: sidebarWidth }}
          className={`flex-shrink-0 border-r overflow-hidden flex flex-col ${isDark ? "border-gray-700 bg-gray-800/50" : "border-gray-200 bg-gray-50"}`}
        >
          <div className={`px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider flex items-center ${isDark ? "text-gray-500" : "text-gray-400"}`}>
            {t("skillEditor.files", "Files")}
            <span className="ml-auto flex items-center gap-0.5">
              <button
                onClick={() => {
                  const name = prompt(t("skillEditor.newFileName", "New file name (e.g. scripts/run.py):"))
                  if (!name || !name.trim()) return
                  const path = name.trim()
                  if (!editor.stageNewFile(path)) {
                    setError(t("skillEditor.fileExists", "File already exists"))
                    return
                  }
                  editor.setCurrentFile(path)
                  editor.setContent("")
                }}
                className={`p-0.5 rounded transition-colors ${isDark ? "hover:bg-gray-700 hover:text-gray-300" : "hover:bg-gray-200 hover:text-gray-600"}`}
                title={t("skillEditor.newFile", "New File")}
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => {
                  const name = prompt(t("skillEditor.newFolderTitle", "New folder name:"))
                  if (!name || !name.trim()) return
                  const path = `${name.trim()}/untitled`
                  if (!editor.stageNewFile(path, "")) return
                  editor.setCurrentFile(path)
                  editor.setContent("")
                }}
                className={`p-0.5 rounded transition-colors ${isDark ? "hover:bg-gray-700 hover:text-gray-300" : "hover:bg-gray-200 hover:text-gray-600"}`}
                title={t("skillEditor.newFolder", "New Folder")}
              >
                <FolderPlus className="w-3.5 h-3.5" />
              </button>
            </span>
          </div>
          <div className="flex-1 overflow-hidden min-h-0" ref={treeContainerRef}>
            <Tree<TreeNode>
              data={editor.treeData}
              openByDefault
              width={sidebarWidth}
              height={treeHeight}
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

      {/* Delete file confirm */}
      {confirmDeletePath && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDeletePath(null)}>
          <div className={`rounded-xl shadow-2xl p-5 max-w-sm mx-4 ${isDark ? "bg-gray-800" : "bg-white"}`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.deleteFile")}</p>
            <p className="text-xs text-gray-500 mb-4">{t("skillEditor.deleteConfirm", { defaultValue: `Delete ${confirmDeletePath}?` })}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDeletePath(null)} className={`px-3 py-1.5 text-xs rounded-lg ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"}`}>{t("common.cancel")}</button>
              <button onClick={confirmDeleteFile} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
