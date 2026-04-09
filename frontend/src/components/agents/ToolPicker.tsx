/**
 * ToolPicker — Modal to browse and select tools from the tool library.
 */
import { useState, useEffect } from "react"
import { useTranslation } from "react-i18next"
import { X, Search, Code2, Loader2 } from "lucide-react"
import { useToolLibraryStore } from "../../stores/tool-library-store"

interface ToolPickerProps {
  open: boolean
  onClose: () => void
  onSelect: (code: string) => void
  existingToolNames: string[]
}

export default function ToolPicker({ open, onClose, onSelect, existingToolNames }: ToolPickerProps) {
  const { t } = useTranslation()
  const { tools, loading, fetchTools } = useToolLibraryStore()
  const [search, setSearch] = useState("")

  useEffect(() => {
    if (open) fetchTools()
  }, [open, fetchTools])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [open, onClose])

  if (!open) return null

  const existingSet = new Set(existingToolNames)

  const filtered = tools.filter(t => {
    const q = search.toLowerCase()
    return t.name.toLowerCase().includes(q) ||
      (t.description || "").toLowerCase().includes(q) ||
      t.id.toLowerCase().includes(q)
  })

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-xl w-[600px] max-h-[70vh] flex flex-col shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <Code2 className="w-4 h-4 text-blue-500" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t("agentEditor.addTool")}</span>
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
              placeholder={t("tools.searchPlaceholder")}
              autoFocus
              className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
            />
          </div>
        </div>

        {/* Tool list */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          {loading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-gray-300" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-center text-xs text-gray-400 py-8">
              {search ? t("tools.noMatching") : t("tools.noTools")}
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              {filtered.map(tool => {
                const alreadyAdded = existingSet.has(tool.id)
                return (
                  <button
                    key={tool.id}
                    onClick={() => {
                      if (alreadyAdded) return
                      onSelect(tool.code)
                      onClose()
                    }}
                    disabled={alreadyAdded}
                    className={`text-left p-3 rounded-lg border transition-all ${
                      alreadyAdded
                        ? "border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 opacity-50 cursor-not-allowed"
                        : "border-gray-200 dark:border-gray-700 hover:border-blue-400 hover:bg-blue-50/50 dark:hover:bg-blue-900/20 cursor-pointer"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <Code2 className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
                      <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">{tool.name}</span>
                      {alreadyAdded && (
                        <span className="ml-auto text-[10px] text-gray-400 flex-shrink-0">{t("agentSkills.added")}</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 dark:text-gray-400 line-clamp-2 ml-5.5">
                      {tool.description || t("tools.descPlaceholder")}
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
