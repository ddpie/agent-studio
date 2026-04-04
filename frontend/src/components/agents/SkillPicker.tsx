/**
 * SkillPicker — Modal to browse and select skills from the global skill library.
 */
import { useState, useEffect } from "react"
import { X, Search, Package, Loader2 } from "lucide-react"
import { listSkills, type SkillIndexEntry } from "../../lib/skill-storage"

interface SkillPickerProps {
  open: boolean
  onClose: () => void
  onSelect: (skill: SkillIndexEntry) => void
  existingSkillSourceIds: string[]
}

export default function SkillPicker({ open, onClose, onSelect, existingSkillSourceIds }: SkillPickerProps) {
  const [skills, setSkills] = useState<SkillIndexEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState("")

  useEffect(() => {
    if (!open) return
    setLoading(true)
    listSkills().then(list => {
      setSkills(list)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [open])

  // ESC to close
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, onClose])

  if (!open) return null

  const filtered = skills.filter(s => {
    const q = search.toLowerCase()
    return s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q)
  })

  const existingSet = new Set(existingSkillSourceIds)

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-xl w-[600px] max-h-[70vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Package className="w-4 h-4 text-blue-500" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">Add Skill</span>
          </div>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 py-2 border-b border-gray-100 dark:border-gray-800">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search skills..."
              autoFocus
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            />
          </div>
        </div>

        {/* Skill list */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-gray-300" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-center text-xs text-gray-400 py-8">
              {search ? "No skills match your search" : "No skills available"}
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {filtered.map(skill => {
                const alreadyBound = existingSet.has(skill.id)
                return (
                  <button
                    key={skill.id}
                    onClick={() => {
                      if (alreadyBound) return
                      onSelect(skill)
                      onClose()
                    }}
                    disabled={alreadyBound}
                    className={`text-left p-3 rounded-lg border transition-all ${
                      alreadyBound
                        ? "border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 opacity-50 cursor-not-allowed"
                        : "border-gray-200 dark:border-gray-700 hover:border-blue-400 hover:bg-blue-50/50 dark:hover:bg-blue-900/20 cursor-pointer"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <Package className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                      <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">{skill.name}</span>
                      {alreadyBound && (
                        <span className="ml-auto text-[10px] text-gray-400 flex-shrink-0">Added</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2 ml-5.5">
                      {skill.description || "No description"}
                    </p>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
