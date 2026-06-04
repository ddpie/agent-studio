/**
 * SkillDiffModal — Diff preview + editable merge for template updates.
 * Left: global template latest (readonly). Right: agent's version (editable).
 */
import { useState, useEffect, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { Save, Loader2 } from "lucide-react"
import { LazyDiffEditor as DiffEditor } from "../ui/LazyMonaco"
import { useUISettings } from "../../stores/ui-settings-store"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { getSkillFile, listSkillFiles as listGlobalSkillFiles } from "../../lib/skill-storage"
import {
  readAgentSkillFile,
  writeAgentSkillFile,
  readAllAgentSkillFiles,
  computeSkillHash,
} from "../../lib/agent-skill-storage"
import type { AgentSkillEntry } from "../../lib/agent-metadata"

function useIsDark() {
  const { theme } = useUISettings()
  if (theme === "dark") return true
  if (theme === "light") return false
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
}

interface SkillDiffModalProps {
  open: boolean
  onClose: () => void
  agentId: string
  skill: AgentSkillEntry
}

export default function SkillDiffModal({ open, onClose, agentId, skill }: SkillDiffModalProps) {
  const { t } = useTranslation()
  const isDark = useIsDark()
  const { updateSkillEntry } = useAgentEditStore()
  const [activeFile, setActiveFile] = useState("SKILL.md")
  const [globalContent, setGlobalContent] = useState<Record<string, string>>({})
  const [localContent, setLocalContent] = useState<Record<string, string>>({})
  const [modifiedContent, setModifiedContent] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load both global and local file contents
  useEffect(() => {
    if (!open) return
    setLoading(true)

    const loadFiles = async () => {
      const globalFiles: Record<string, string> = {}
      const localFiles: Record<string, string> = {}

      // Load global template files
      const globalMd = await getSkillFile(skill.sourceSkillId, "SKILL.md")
      if (globalMd) globalFiles["SKILL.md"] = globalMd
      const globalExtra = await listGlobalSkillFiles(skill.sourceSkillId)
      for (const f of globalExtra) {
        const content = await getSkillFile(skill.sourceSkillId, f)
        if (content !== null) globalFiles[f] = content
      }

      // Load local agent skill files
      for (const f of skill.files) {
        const content = await readAgentSkillFile(agentId, skill.id, f)
        if (content !== null) localFiles[f] = content
      }

      setGlobalContent(globalFiles)
      setLocalContent(localFiles)
      setModifiedContent({ ...localFiles })
      setLoading(false)
    }

    loadFiles()
  }, [open, agentId, skill])

  // ESC to close
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, onClose])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      // Write all modified files back to S3
      for (const [filePath, content] of Object.entries(modifiedContent)) {
        if (content !== localContent[filePath]) {
          const success = await writeAgentSkillFile(agentId, skill.id, filePath, content)
          if (!success) throw new Error(t("agentSkills.failedToSaveFile", { file: filePath }))
        }
      }

      // Recompute contentHash
      const allFiles = await readAllAgentSkillFiles(agentId, skill.id)
      for (const [f, c] of Object.entries(modifiedContent)) {
        allFiles[f] = c
      }
      const newHash = await computeSkillHash(allFiles)

      // Compute current global hash to update sourceContentHash
      const globalHash = await computeSkillHash(globalContent)

      updateSkillEntry(skill.id, {
        contentHash: newHash,
        sourceContentHash: globalHash,
      })

      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToSaveChanges"))
    } finally {
      setSaving(false)
    }
  }, [modifiedContent, localContent, globalContent, agentId, skill, updateSkillEntry, onClose])

  if (!open) return null

  // Collect all unique file names from both sides
  const allFileNames = [...new Set([...Object.keys(globalContent), ...Object.keys(localContent)])].sort()

  const currentGlobal = globalContent[activeFile] || ""
  const currentModified = modifiedContent[activeFile] || ""

  const lang = activeFile.endsWith(".py") ? "python"
    : activeFile.endsWith(".md") ? "markdown"
    : activeFile.endsWith(".json") ? "json"
    : activeFile.endsWith(".sh") ? "shell"
    : "plaintext"

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">
              {t("agentSkills.templateUpdatesTitle", { name: skill.name })}
            </span>
            <div className="flex items-center gap-1">
              {allFileNames.map(f => (
                <button
                  key={f}
                  onClick={() => setActiveFile(f)}
                  className={`px-2 py-0.5 text-[11px] rounded ${
                    activeFile === f
                      ? "bg-blue-600 text-white"
                      : "text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
                  }`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1 px-4 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
              {t("agentSkills.save")}
            </button>
          </div>
        </div>

        {/* Labels */}
        <div className="px-4 py-1.5 text-xs border-b flex text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
          <span className="flex-1">{t("agentSkills.globalTemplate")}</span>
          <span className="flex-1 text-right">{t("agentSkills.myVersion")}</span>
        </div>

        {error && (
          <div className="px-4 py-2 text-xs text-red-600 bg-red-50 dark:bg-red-900/20 dark:text-red-400 border-b border-red-200 dark:border-red-800">
            {error}
          </div>
        )}

        {/* Diff editor */}
        <div className="flex-1 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
            </div>
          ) : (
            <DiffEditor
              original={currentGlobal}
              modified={currentModified}
              language={lang}
              theme={isDark ? "vs-dark" : "light"}
              keepCurrentOriginalModel={true}
              keepCurrentModifiedModel={true}
              onMount={(editor) => {
                const modifiedEditor = editor.getModifiedEditor()
                modifiedEditor.onDidChangeModelContent(() => {
                  const val = modifiedEditor.getValue()
                  setModifiedContent(prev => ({ ...prev, [activeFile]: val }))
                })
              }}
              options={{
                readOnly: false,
                originalEditable: false,
                renderSideBySide: true,
                fontSize: 12,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                automaticLayout: true,
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
