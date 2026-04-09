/**
 * SkillsSection — Skills management section in AgentEditForm.
 * Shows bound skills as cards with status badges and actions.
 */
import { useState, useEffect, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { Package, Plus, Trash2, RefreshCw, Loader2 } from "lucide-react"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import {
  readGlobalSkillFiles,
  deleteAgentSkill,
} from "../../lib/agent-skill-storage"
import { listSkills, type SkillIndexEntry } from "../../lib/skill-storage"
import type { AgentSkillEntry } from "../../lib/agent-metadata"
import SkillPicker from "./SkillPicker"
interface SkillsSectionProps {
  skills: AgentSkillEntry[]
  agentId: string
  deployedHashes?: Record<string, string>
  onEditSkill?: (skill: AgentSkillEntry) => void
}

export default function SkillsSection({ skills, agentId, deployedHashes, onEditSkill }: SkillsSectionProps) {
  const { t } = useTranslation()
  const { addSkill, removeSkill, setPendingSkillFiles, initSkillFiles } = useAgentEditStore()
  const [pickerOpen, setPickerOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addProgress, setAddProgress] = useState("")
  const [deleting, setDeleting] = useState<string | null>(null)
  const [globalSkills, setGlobalSkills] = useState<SkillIndexEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showCreateDialog, setShowCreateDialog] = useState(false)
  const [createName, setCreateName] = useState("")
  const [createDesc, setCreateDesc] = useState("")
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  // Load global skill index for template update detection
  useEffect(() => {
    listSkills().then(setGlobalSkills).catch(err => {
      console.warn("Failed to load global skills for template detection:", err)
    })
  }, [])

  const handleOpenSkill = (skill: AgentSkillEntry) => {
    onEditSkill?.(skill)
  }

  const handleAddSkill = useCallback(async (globalSkill: SkillIndexEntry) => {
    setAdding(true)
    setAddProgress("")
    setError(null)
    try {
      const { entry, files } = await readGlobalSkillFiles(globalSkill, (done, total) => {
        setAddProgress(`${done}/${total}`)
      })
      addSkill(entry)
      initSkillFiles(entry.id, {})  // empty baseline so diff shows all files as added
      setPendingSkillFiles(entry.id, files)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToAdd"))
    } finally {
      setAdding(false)
      setAddProgress("")
    }
  }, [addSkill, setPendingSkillFiles, t])

  const handleDeleteSkill = useCallback(async (skillId: string) => {
    setConfirmDeleteId(skillId)
  }, [])

  const confirmDeleteSkill = useCallback(async () => {
    if (!confirmDeleteId) return
    setDeleting(confirmDeleteId)
    setConfirmDeleteId(null)
    setError(null)
    try {
      await deleteAgentSkill(agentId, confirmDeleteId)
      removeSkill(confirmDeleteId)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToRemove"))
    } finally {
      setDeleting(null)
    }
  }, [agentId, confirmDeleteId, removeSkill, t])

  const existingSourceIds = skills.map(s => s.sourceSkillId)

  const handleCreateNewSkill = useCallback(() => {
    if (!createName.trim()) return
    const newId = crypto.randomUUID().slice(0, 8)
    const name = createName.trim().replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase()
    const desc = createDesc.trim()
    const content = `---\nname: "${name}"\ndescription: "${desc}"\ntype: "prompt"\nsource: "manual"\nuser-invocable: true\n---\n\n# ${createName.trim()}\n\n${desc || "TODO: Add skill instructions here."}\n`
    const entry: AgentSkillEntry = {
      id: newId,
      sourceSkillId: newId,
      sourceContentHash: "",
      name,
      description: desc,
      contentHash: "",
      files: [],
    }
    addSkill(entry)
    setPendingSkillFiles(newId, { "SKILL.md": content })
    setShowCreateDialog(false)
    setCreateName("")
    setCreateDesc("")
    onEditSkill?.(entry)
  }, [addSkill, setPendingSkillFiles, onEditSkill, createName, createDesc])

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-gray-50 dark:from-gray-800 to-white dark:to-gray-800 flex items-center gap-1.5">
        <span className="text-gray-400"><Package className="w-3.5 h-3.5" /></span>
        <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t("agentSkills.title")}</h3>
        <span className="ml-auto" />
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
            {t("agentSkills.noSkills")}
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

            return (
              <div
                key={skill.id}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 cursor-pointer transition-all"
                onClick={() => handleOpenSkill(skill)}
              >
                <Package className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">{skill.name}</span>
                <span className="text-[11px] text-gray-400 truncate flex-1">{skill.description}</span>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  {hasTemplateUpdate && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        window.dispatchEvent(new CustomEvent("open-skill-diff", {
                          detail: { agentId, skill, sourceSkillId: skill.sourceSkillId }
                        }))
                      }}
                      className="flex items-center gap-1 px-1.5 py-0.5 text-[10px] text-orange-600 hover:bg-orange-50 dark:hover:bg-orange-900/20 rounded transition-colors"
                      title={t("agentSkills.templateUpdated")}
                    >
                      <RefreshCw className="w-3 h-3" />
                    </button>
                  )}
                  {hasLocalChanges && (
                    <span className="w-2 h-2 rounded-full bg-blue-400" title={t("agentSkills.localChanges")} />
                  )}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDeleteSkill(skill.id)
                    }}
                    disabled={deleting === skill.id}
                    className="p-1 text-gray-300 hover:text-red-500 transition-colors"
                    title={t("agentSkills.remove")}
                  >
                    {deleting === skill.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                  </button>
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Bottom action buttons */}
      <div className="px-3 pb-3 flex gap-2">
        <button
          onClick={() => setPickerOpen(true)}
          disabled={adding}
          className="flex items-center gap-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 px-3 py-2 border border-dashed border-blue-300 dark:border-blue-700 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-900/30 transition-colors flex-1 justify-center disabled:opacity-70"
        >
          {adding ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
          {adding && addProgress ? addProgress : t("agentSkills.add")}
        </button>
        <button
          onClick={() => setShowCreateDialog(true)}
          className="flex items-center gap-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors flex-1 justify-center"
        >
          <Plus className="w-3.5 h-3.5" /> {t("agentSkills.createNew")}
        </button>
      </div>

      {/* Skill Picker Modal */}
      <SkillPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelect={handleAddSkill}
        existingSkillSourceIds={existingSourceIds}
      />

      {/* Create Skill Dialog */}
      {showCreateDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => { setShowCreateDialog(false); setCreateName("") }}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium mb-3 text-gray-800 dark:text-gray-200">{t("agentSkills.createNew")}</p>
            <div className="space-y-3">
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("skills.name")}</label>
                <input
                  autoFocus
                  value={createName}
                  onChange={e => setCreateName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && createName.trim()) handleCreateNewSkill(); if (e.key === "Escape") { setShowCreateDialog(false); setCreateName(""); setCreateDesc("") } }}
                  placeholder={t("skills.namePlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("skills.description")}</label>
                <input
                  value={createDesc}
                  onChange={e => setCreateDesc(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && createName.trim()) handleCreateNewSkill(); if (e.key === "Escape") { setShowCreateDialog(false); setCreateName(""); setCreateDesc("") } }}
                  placeholder={t("skills.descPlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => { setShowCreateDialog(false); setCreateName(""); setCreateDesc("") }} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">{t("common.cancel")}</button>
              <button onClick={handleCreateNewSkill} disabled={!createName.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("common.create")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Skill Confirm */}
      {confirmDeleteId && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmDeleteId(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium mb-1 text-gray-800 dark:text-gray-200">{t("agentSkills.removeSkill")}</p>
            <p className="text-xs text-gray-500 mb-4">{t("agentSkills.removeConfirm")}</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmDeleteId(null)} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">{t("common.cancel")}</button>
              <button onClick={confirmDeleteSkill} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}

    </div>
  )
}
