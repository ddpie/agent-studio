/**
 * SkillEditorModal — Full-screen modal for editing agent-private skill files.
 * File tree sidebar + Monaco editor, no routing required.
 */
import { useState, useEffect, useCallback } from "react"
import { X, Save, Loader2, FileText, File as FileIcon } from "lucide-react"
import MonacoEditor from "@monaco-editor/react"
import { useTranslation } from "react-i18next"
import { useUISettings } from "../../stores/ui-settings-store"
import { useSkillStorage } from "../../hooks/useSkillStorage"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { computeSkillHash, readAllAgentSkillFiles } from "../../lib/agent-skill-storage"

function useIsDark() {
  const { theme } = useUISettings()
  if (theme === "dark") return true
  if (theme === "light") return false
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
}

function getLanguage(filename: string): string {
  if (filename.endsWith(".py")) return "python"
  if (filename.endsWith(".md")) return "markdown"
  if (filename.endsWith(".json")) return "json"
  if (filename.endsWith(".sh")) return "shell"
  if (filename.endsWith(".js")) return "javascript"
  if (filename.endsWith(".ts")) return "typescript"
  if (filename.endsWith(".yaml") || filename.endsWith(".yml")) return "yaml"
  return "plaintext"
}

interface SkillEditorModalProps {
  open: boolean
  onClose: () => void
  agentId: string
  skillId: string
  sourceSkillId: string
  skillName: string
}

export default function SkillEditorModal({ open, onClose, agentId, skillId, sourceSkillId, skillName }: SkillEditorModalProps) {
  const { t } = useTranslation()
  const isDark = useIsDark()
  const storage = useSkillStorage(sourceSkillId, agentId, skillId)
  const { updateSkillEntry } = useAgentEditStore()

  const [files, setFiles] = useState<string[]>([])
  const [currentFile, setCurrentFile] = useState("SKILL.md")
  const [content, setContent] = useState("")
  const [originalContent, setOriginalContent] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load file list on open
  useEffect(() => {
    if (!open) return
    setLoading(true)
    setError(null)
    Promise.all([
      storage.listFiles(),
      storage.getContent()
    ]).then(([fileList, skillMd]) => {
      const allFiles = ["SKILL.md", ...fileList.filter((f: string) => f !== "SKILL.md")]
      setFiles([...new Set(allFiles)])
      setContent(skillMd || "")
      setOriginalContent(skillMd || "")
      setCurrentFile("SKILL.md")
      setDirty(false)
      setLoading(false)
    }).catch(() => {
      setError(t("agentSkills.failedToLoad"))
      setLoading(false)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, agentId, skillId, sourceSkillId])

  // Load file content when switching files
  const loadFile = useCallback(async (path: string) => {
    if (path === currentFile && !loading) return
    if (dirty) {
      if (!confirm(t("skillEditor.unsavedChanges", "Unsaved changes") + " — " + t("common.discard", "Discard") + "?")) return
    }
    setCurrentFile(path)
    setLoading(true)
    setError(null)
    try {
      const fileContent = await storage.getFile(path)
      setContent(fileContent || "")
      setOriginalContent(fileContent || "")
      setDirty(false)
    } catch {
      setError(t("agentSkills.failedToLoad"))
    } finally {
      setLoading(false)
    }
  }, [currentFile, dirty, storage, t, loading])

  // Save current file
  const handleSave = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const success = await storage.writeFile(currentFile, content)
      if (!success) throw new Error("Write failed")
      setOriginalContent(content)
      setDirty(false)
      // Recompute hash
      const allFiles = await readAllAgentSkillFiles(agentId, skillId)
      allFiles[currentFile] = content
      const newHash = await computeSkillHash(allFiles)
      updateSkillEntry(skillId, { contentHash: newHash })
    } catch {
      setError(t("agentSkills.failedToSave", "Failed to save"))
    } finally {
      setSaving(false)
    }
  }, [currentFile, content, storage, agentId, skillId, updateSkillEntry, t])

  // ESC to close
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (dirty) {
          if (confirm(t("skillEditor.unsavedChanges", "Unsaved changes") + " — " + t("common.discard", "Discard") + "?")) {
            onClose()
          }
        } else {
          onClose()
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, onClose, dirty, t])

  // Ctrl+S / Cmd+S to save
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault()
        if (dirty) handleSave()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, dirty, handleSave])

  const handleClose = () => {
    if (dirty) {
      if (!confirm(t("skillEditor.unsavedChanges", "Unsaved changes") + " — " + t("common.discard", "Discard") + "?")) return
    }
    onClose()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center" onClick={handleClose}>
      <div
        className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-xl w-[90vw] h-[85vh] flex flex-col shadow-2xl overflow-hidden`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`flex items-center gap-3 px-4 py-2.5 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
          <FileText className="w-4 h-4 text-blue-500" />
          <span className={`text-sm font-semibold ${isDark ? "text-gray-200" : "text-gray-800"}`}>{skillName}</span>
          <span className="text-[10px] px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-full">
            Agent Copy
          </span>
          <div className="flex-1" />
          {error && (
            <span className="text-xs text-red-500">{error}</span>
          )}
          <button
            onClick={handleSave}
            disabled={!dirty || saving}
            className="flex items-center gap-1 px-3 py-1 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors"
          >
            {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            {t("common.save", "Save")}
          </button>
          <button onClick={handleClose} className={`p-1 rounded ${isDark ? "hover:bg-gray-800 text-gray-400" : "hover:bg-gray-100 text-gray-500"}`}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex flex-1 overflow-hidden">
          {/* File sidebar */}
          <div className={`w-48 flex-shrink-0 border-r overflow-y-auto ${isDark ? "border-gray-700 bg-gray-800/50" : "border-gray-200 bg-gray-50"}`}>
            <div className={`px-3 py-2 text-[10px] font-semibold uppercase tracking-wider ${isDark ? "text-gray-500" : "text-gray-400"}`}>
              {t("skillEditor.files", "Files")}
            </div>
            {files.map(f => (
              <button
                key={f}
                onClick={() => loadFile(f)}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 transition-colors ${
                  currentFile === f
                    ? isDark ? "bg-blue-900/30 text-blue-400" : "bg-blue-50 text-blue-600"
                    : isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                <FileIcon className="w-3 h-3 flex-shrink-0" />
                <span className="truncate">{f}</span>
                {f === currentFile && dirty && (
                  <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0 ml-auto" />
                )}
              </button>
            ))}
          </div>

          {/* Editor */}
          <div className="flex-1 overflow-hidden">
            {loading ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
              </div>
            ) : (
              <MonacoEditor
                height="100%"
                value={content}
                onChange={val => {
                  setContent(val || "")
                  setDirty(val !== originalContent)
                }}
                language={getLanguage(currentFile)}
                theme={isDark ? "vs-dark" : "light"}
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
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
