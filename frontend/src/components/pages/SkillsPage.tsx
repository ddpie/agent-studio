import { useEffect, useState, useCallback, useRef } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  Package, Search, RefreshCw, Loader2, FileText, Upload, Trash2, RotateCcw, X, Plus,
} from "lucide-react";
import { listSkills, listDeletedSkills, importSkill, restoreSkill, permanentlyDeleteSkill, type SkillIndexEntry } from "../../lib/skill-storage";

export default function SkillsPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillIndexEntry[]>([]);
  const [trashedSkills, setTrashedSkills] = useState<SkillIndexEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [importing, setImporting] = useState(false);
  const [showTrash, setShowTrash] = useState(false);
  const [confirmPermanentDelete, setConfirmPermanentDelete] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createDesc, setCreateDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    Promise.all([listSkills(), listDeletedSkills()]).then(([s, tr]) => {
      setSkills(s);
      setTrashedSkills(tr);
      setLoading(false);
    });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleFileImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImporting(true);
    try {
      const content = await file.text();
      const name = file.name.replace(/\.(md|txt|cursorrules)$/i, "").replace(/[^a-zA-Z0-9-]/g, "-").toLowerCase();
      const result = await importSkill(content, name, `Imported from ${file.name}`);
      if (result) refresh();
      else alert(t("skills.importFailed"));
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleRestore = async (id: string) => {
    await restoreSkill(id);
    refresh();
  };

  const handlePermanentDelete = async (id: string) => {
    await permanentlyDeleteSkill(id);
    setConfirmPermanentDelete(null);
    refresh();
  };

  const handleCreate = async () => {
    if (!createName.trim()) return;
    setCreating(true);
    const name = createName.trim().replace(/[^a-zA-Z0-9_-]/g, "-").toLowerCase();
    const desc = createDesc.trim();
    const content = `---
name: "${name}"
description: "${desc}"
type: "prompt"
source: "manual"
user-invocable: true
---

# ${createName.trim()}

${desc || "TODO: Add skill instructions here."}
`;
    const result = await importSkill(content, name, desc);
    setCreating(false);
    setShowCreate(false);
    setCreateName("");
    setCreateDesc("");
    if (result) {
      navigate(`/skills/${result.id}`);
    }
  };

  const filtered = (showTrash ? trashedSkills : skills).filter(
    (s) => !search || s.name.toLowerCase().includes(search.toLowerCase())
      || (s.description || "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">{t("skills.title")}</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t("skills.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder={t("skills.searchPlaceholder")}
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder:text-gray-400" />
          </div>
          <button onClick={() => setShowTrash(!showTrash)}
            className={`p-1.5 rounded-lg transition-colors ${showTrash
              ? "text-red-500 bg-red-50 dark:bg-red-900/20"
              : "text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"
            }`}
            title={showTrash ? t("common.back") : t("skills.trash")}>
            <Trash2 className="w-4 h-4" />
          </button>
          <button onClick={refresh}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
          {!showTrash && (
            <>
              <input ref={fileInputRef} type="file" accept=".md,.txt,.cursorrules" className="hidden" onChange={handleFileImport} />
              <button onClick={() => fileInputRef.current?.click()} disabled={importing}
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 disabled:opacity-50">
                {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                {t("common.import")}
              </button>
              <button onClick={() => setShowCreate(true)}
                className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600">
                <Plus className="w-3.5 h-3.5" />
                {t("common.create")}
              </button>
            </>
          )}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <Package className="w-12 h-12 mb-3 opacity-30" />
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {search ? t("skills.noMatching") : showTrash ? t("skills.trashEmpty") : t("skills.noSkills")}
            </p>
            {!showTrash && <p className="text-xs mt-1">{t("skills.createHint")}</p>}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((skill) => (
              <div key={skill.id}
                className={`border rounded-lg p-4 transition-all text-left ${showTrash
                  ? "border-gray-200 dark:border-gray-700 opacity-60"
                  : "border-gray-200 dark:border-gray-700 hover:border-blue-300 hover:shadow-sm cursor-pointer"
                }`}
                onClick={showTrash ? undefined : () => navigate(`/skills/${skill.id}`)}
              >
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-blue-500 flex-shrink-0" />
                  <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100 flex-1 truncate">{skill.name}</h3>
                  {showTrash && (
                    <div className="flex items-center gap-1">
                      <button onClick={() => handleRestore(skill.id)}
                        className="p-1 text-green-500 hover:bg-green-50 dark:hover:bg-green-900/20 rounded transition-colors"
                        title={t("skills.restore")}>
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => setConfirmPermanentDelete(skill.id)}
                        className="p-1 text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                        title={t("skills.deletePermanently")}>
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}
                </div>
                {skill.description && (
                  <p className="text-xs text-gray-500 mt-2 line-clamp-2">{skill.description}</p>
                )}
                {showTrash && skill.deletedAt && (
                  <p className="text-[10px] text-gray-400 mt-1">
                    {t("skills.deleted", { date: new Date(skill.deletedAt).toLocaleDateString() })}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Permanent delete confirm */}
      {confirmPermanentDelete && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setConfirmPermanentDelete(null)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium mb-1 text-gray-800 dark:text-gray-200">{t("skills.deletePermanently")}?</p>
            <p className="text-xs text-gray-500 mb-4">
              {t("skills.permanentDeleteConfirm")}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmPermanentDelete(null)} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">{t("common.cancel")}</button>
              <button onClick={() => handlePermanentDelete(confirmPermanentDelete)} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("skills.deletePermanently")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Create skill dialog */}
      {showCreate && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setShowCreate(false)}>
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80" onClick={e => e.stopPropagation()}>
            <p className="text-sm font-medium mb-3 text-gray-800 dark:text-gray-200">{t("skills.createSkill")}</p>
            <div className="space-y-3">
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("skills.name")}</label>
                <input
                  autoFocus
                  value={createName}
                  onChange={e => setCreateName(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && createName.trim()) handleCreate(); if (e.key === "Escape") setShowCreate(false); }}
                  placeholder={t("skills.namePlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="text-[11px] text-gray-500 mb-1 block">{t("skills.description")}</label>
                <input
                  value={createDesc}
                  onChange={e => setCreateDesc(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && createName.trim()) handleCreate(); if (e.key === "Escape") setShowCreate(false); }}
                  placeholder={t("skills.descPlaceholder")}
                  className="w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowCreate(false)} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">{t("common.cancel")}</button>
              <button onClick={handleCreate} disabled={!createName.trim() || creating}
                className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">
                {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : t("common.create")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
