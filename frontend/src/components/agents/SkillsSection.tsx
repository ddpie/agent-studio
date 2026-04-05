/**
 * SkillsSection — Skills management section in AgentEditForm.
 * Shows bound skills as cards with status badges, inline Monaco editor, and actions.
 */
import { useState, useEffect, useCallback } from "react"
import { Package, Plus, Trash2, RefreshCw, ChevronDown, ChevronRight, Loader2 } from "lucide-react"
import MonacoEditor from "@monaco-editor/react"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import { useUISettings } from "../../stores/ui-settings-store"
import {
  copySkillToAgent,
  readAgentSkillFile,
  writeAgentSkillFile,
  deleteAgentSkill,
  readAllAgentSkillFiles,
  computeSkillHash,
} from "../../lib/agent-skill-storage"
import { listSkills, type SkillIndexEntry } from "../../lib/skill-storage"
import type { AgentSkillEntry } from "../../lib/agent-metadata"
import SkillPicker from "./SkillPicker"

function useIsDark() {
  const { theme } = useUISettings()
  if (theme === "dark") return true
  if (theme === "light") return false
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
}

interface SkillsSectionProps {
  skills: AgentSkillEntry[]
  agentId: string
  deployedHashes?: Record<string, string>
}

export default function SkillsSection({ skills, agentId, deployedHashes }: SkillsSectionProps) {
  const isDark = useIsDark()
  const { addSkill, removeSkill, updateSkillEntry } = useAgentEditStore()
  const [pickerOpen, setPickerOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [activeFile, setActiveFile] = useState<string>("SKILL.md")
  const [fileContent, setFileContent] = useState<string>("")
  const [fileDirty, setFileDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [adding, setAdding] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [globalSkills, setGlobalSkills] = useState<SkillIndexEntry[]>([])
  const [error, setError] = useState<string | null>(null)

  // Load global skill index for template update detection
  useEffect(() => {
    listSkills().then(setGlobalSkills).catch(err => {
      console.warn("Failed to load global skills for template detection:", err)
    })
  }, [])

  // Load file content when expanding a skill or switching file tab
  useEffect(() => {
    if (!expandedId) return
    setFileContent("")
    setFileDirty(false)
    setError(null)
    readAgentSkillFile(agentId, expandedId, activeFile).then(content => {
      if (content === null) {
        setError("Failed to load file content")
      } else {
        setFileContent(content)
      }
    }).catch(() => {
      setError("Failed to load file content")
    })
  }, [expandedId, activeFile, agentId])

  const handleAddSkill = useCallback(async (globalSkill: SkillIndexEntry) => {
    setAdding(true)
    setError(null)
    try {
      const entry = await copySkillToAgent(agentId, globalSkill)
      addSkill(entry)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add skill")
    } finally {
      setAdding(false)
    }
  }, [agentId, addSkill])

  const handleDeleteSkill = useCallback(async (skillId: string) => {
    if (!confirm("Remove this skill from the agent?")) return
    setDeleting(skillId)
    setError(null)
    try {
      await deleteAgentSkill(agentId, skillId)
      removeSkill(skillId)
      if (expandedId === skillId) setExpandedId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove skill")
    } finally {
      setDeleting(null)
    }
  }, [agentId, removeSkill, expandedId])

  const handleSaveFile = useCallback(async () => {
    if (!expandedId || !fileDirty) return
    setSaving(true)
    setError(null)
    try {
      const success = await writeAgentSkillFile(agentId, expandedId, activeFile, fileContent)
      if (!success) throw new Error("Failed to save file")
      const allFiles = await readAllAgentSkillFiles(agentId, expandedId)
      allFiles[activeFile] = fileContent
      const newHash = await computeSkillHash(allFiles)
      updateSkillEntry(expandedId, { contentHash: newHash })
      setFileDirty(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save skill file")
    } finally {
      setSaving(false)
    }
  }, [expandedId, activeFile, fileContent, fileDirty, agentId, updateSkillEntry])

  const toggleExpand = (id: string) => {
    if (expandedId === id) {
      setExpandedId(null)
    } else {
      setExpandedId(id)
      setActiveFile("SKILL.md")
    }
  }

  const existingSourceIds = skills.map(s => s.sourceSkillId)

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-gray-50 dark:from-gray-800 to-white dark:to-gray-800 flex items-center gap-1.5">
        <span className="text-gray-400"><Package className="w-3.5 h-3.5" /></span>
        <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Skills</h3>
        <span className="ml-auto">
          <button
            onClick={() => setPickerOpen(true)}
            disabled={adding}
            className="flex items-center gap-1 px-2 py-0.5 text-[11px] text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded transition-colors disabled:opacity-50"
          >
            {adding ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
            Add
          </button>
        </span>
      </div>

      <div className="px-3 py-3 space-y-2">
        {error && (
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-red-600 bg-red-50 dark:bg-red-900/20 dark:text-red-400 rounded-lg">
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-auto text-red-400 hover:text-red-600">✕</button>
          </div>
        )}
        {skills.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-4">
            No skills bound. Click "+ Add" to attach skills from the library.
          </p>
        ) : (
          skills.map(skill => {
            const globalSkill = globalSkills.find(g => g.id === skill.sourceSkillId)
            const hasTemplateUpdate = globalSkill && globalSkill.contentHash
              ? globalSkill.contentHash !== skill.sourceContentHash
              : false
            const hasLocalChanges = deployedHashes
              ? deployedHashes[skill.id] !== skill.contentHash
              : false
            const isExpanded = expandedId === skill.id

            return (
              <div key={skill.id} className={`rounded-lg border transition-all ${
                isExpanded
                  ? "border-blue-300 dark:border-blue-700 bg-blue-50/30 dark:bg-blue-900/10"
                  : "border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600"
              }`}>
                {/* Card header */}
                <button
                  onClick={() => toggleExpand(skill.id)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-left"
                >
                  {isExpanded
                    ? <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
                    : <ChevronRight className="w-3 h-3 text-gray-400 flex-shrink-0" />
                  }
                  <Package className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                  <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">{skill.name}</span>
                  <span className="text-[11px] text-gray-400 truncate flex-1">{skill.description}</span>
                  <div className="flex items-center gap-1.5 flex-shrink-0">
                    {hasTemplateUpdate && (
                      <span className="w-2 h-2 rounded-full bg-orange-400" title="Template has updates" />
                    )}
                    {hasLocalChanges && (
                      <span className="w-2 h-2 rounded-full bg-blue-400" title="Local changes not deployed" />
                    )}
                  </div>
                </button>

                {/* Expanded content */}
                {isExpanded && (
                  <div className="px-3 pb-3 space-y-2">
                    {/* File tabs */}
                    <div className="flex items-center gap-1 border-b border-gray-200 dark:border-gray-700">
                      {skill.files.map(f => (
                        <button
                          key={f}
                          onClick={() => setActiveFile(f)}
                          className={`px-2 py-1 text-[11px] border-b-2 transition-colors ${
                            activeFile === f
                              ? "border-blue-500 text-blue-600 font-medium"
                              : "border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                          }`}
                        >
                          {f}
                        </button>
                      ))}
                    </div>

                    {/* Monaco editor */}
                    <div className="rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
                      <MonacoEditor
                        height="300px"
                        value={fileContent}
                        onChange={val => { setFileContent(val || ""); setFileDirty(true) }}
                        language={
                          activeFile.endsWith(".py") ? "python"
                          : activeFile.endsWith(".md") ? "markdown"
                          : activeFile.endsWith(".json") ? "json"
                          : activeFile.endsWith(".sh") ? "shell"
                          : "plaintext"
                        }
                        theme={isDark ? "vs-dark" : "light"}
                        options={{
                          fontSize: 12,
                          minimap: { enabled: false },
                          scrollBeyondLastLine: false,
                          wordWrap: "on",
                          lineNumbers: "on",
                          automaticLayout: true,
                        }}
                      />
                    </div>

                    {/* Action buttons */}
                    <div className="flex items-center gap-2">
                      <button
                        onClick={handleSaveFile}
                        disabled={!fileDirty || saving}
                        className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-30 transition-colors"
                      >
                        {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                        Save
                      </button>
                      {hasTemplateUpdate && (
                        <button
                          onClick={() => {
                            window.dispatchEvent(new CustomEvent("open-skill-diff", {
                              detail: { agentId, skill, sourceSkillId: skill.sourceSkillId }
                            }))
                          }}
                          className="flex items-center gap-1 px-2.5 py-1 text-[11px] text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded-lg transition-colors"
                        >
                          <RefreshCw className="w-3 h-3" />
                          View Template Updates
                        </button>
                      )}
                      <button
                        onClick={() => handleDeleteSkill(skill.id)}
                        disabled={deleting === skill.id}
                        className="flex items-center gap-1 px-2.5 py-1 text-[11px] text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors ml-auto"
                      >
                        {deleting === skill.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                        Remove
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>

      {/* Skill Picker Modal */}
      <SkillPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={handleAddSkill}
        existingSkillSourceIds={existingSourceIds}
      />
    </div>
  )
}
