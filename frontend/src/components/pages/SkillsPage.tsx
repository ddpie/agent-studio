import { useEffect, useState, useCallback } from "react";
import {
  Package, Search, RefreshCw, Loader2, Trash2, ChevronLeft, FileText,
} from "lucide-react";
import { listSkills, getSkillContent, deleteSkill, type SkillIndexEntry } from "../../lib/skill-storage";

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillIndexEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillIndexEntry | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    listSkills().then((s) => { setSkills(s); setLoading(false); });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSelect = async (skill: SkillIndexEntry) => {
    setSelectedSkill(skill);
    setLoadingContent(true);
    const content = await getSkillContent(skill.id);
    setSkillContent(content);
    setLoadingContent(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this skill?")) return;
    await deleteSkill(id);
    setSelectedSkill(null);
    setSkillContent(null);
    refresh();
  };

  const filtered = skills.filter(
    (s) => !search || s.name.toLowerCase().includes(search.toLowerCase())
      || s.description.toLowerCase().includes(search.toLowerCase())
  );

  // Detail view
  if (selectedSkill) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-2 px-6 py-4 border-b border-gray-200">
          <button onClick={() => { setSelectedSkill(null); setSkillContent(null); }}
            className="p-1 hover:bg-gray-100 rounded">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <Package className="w-4 h-4 text-blue-500" />
          <h2 className="text-sm font-semibold text-gray-900">{selectedSkill.name}</h2>
          <div className="flex-1" />
          <button onClick={() => handleDelete(selectedSkill.id)}
            className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {loadingContent ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
            </div>
          ) : skillContent ? (
            <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono leading-relaxed">
              {skillContent}
            </pre>
          ) : (
            <p className="text-sm text-gray-400">Failed to load skill content.</p>
          )}
        </div>
      </div>
    );
  }

  // List view
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Skills</h2>
          <p className="text-xs text-gray-500">Reusable capabilities for your agents</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search skills..."
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none" />
          </div>
          <button onClick={refresh}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
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
            <p className="text-sm font-medium text-gray-600">
              {search ? "No matching skills" : "No skills yet"}
            </p>
            <p className="text-xs mt-1">Create skills through the Meta Agent chat</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((skill) => (
              <button key={skill.id} onClick={() => handleSelect(skill)}
                className="border border-gray-200 rounded-lg p-4 hover:border-blue-300 hover:shadow-sm transition-all text-left">
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-blue-500 flex-shrink-0" />
                  <h3 className="text-sm font-medium text-gray-900">{skill.name}</h3>
                </div>
                {skill.description && (
                  <p className="text-xs text-gray-500 mt-2 line-clamp-2">{skill.description}</p>
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
