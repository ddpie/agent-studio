/**
 * SkillsSection — Skills management section in AgentEditForm.
 * Shows bound skills as cards with status badges and actions.
 */
import { useState, useEffect, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { Package, Plus, Trash2, RefreshCw, Loader2 } from "lucide-react"
import { useAgentEditStore } from "../../stores/agent-edit-store"
import {
  copySkillToAgent,
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
  const { addSkill, removeSkill } = useAgentEditStore()
  const [pickerOpen, setPickerOpen] = useState(false)
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

  const handleOpenSkill = (skill: AgentSkillEntry) => {
    onEditSkill?.(skill)
  }

  const handleAddSkill = useCallback(async (globalSkill: SkillIndexEntry) => {
    setAdding(true)
    setError(null)
    try {
      const entry = await copySkillToAgent(agentId, globalSkill)
      addSkill(entry)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToAdd"))
    } finally {
      setAdding(false)
    }
  }, [agentId, addSkill, t])

  const handleDeleteSkill = useCallback(async (skillId: string) => {
    if (!confirm(t("agentSkills.removeConfirm"))) return
    setDeleting(skillId)
    setError(null)
    try {
      await deleteAgentSkill(agentId, skillId)
      removeSkill(skillId)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("agentSkills.failedToRemove"))
    } finally {
      setDeleting(null)
    }
  }, [agentId, removeSkill, t])

  const existingSourceIds = skills.map(s => s.sourceSkillId)

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-gray-50 dark:from-gray-800 to-white dark:to-gray-800 flex items-center gap-1.5">
        <span className="text-gray-400"><Package className="w-3.5 h-3.5" /></span>
        <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">{t("agentSkills.title")}</h3>
        <span className="ml-auto">
          <button
            onClick={() => setPickerOpen(true)}
            disabled={adding}
            className="flex items-center gap-1 px-2 py-0.5 text-[11px] text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors disabled:opacity-70"
          >
            {adding ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
            {t("agentSkills.add")}
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
