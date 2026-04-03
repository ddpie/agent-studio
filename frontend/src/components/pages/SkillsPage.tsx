import { useEffect, useState, useCallback, useRef } from "react";
import { useNavigate } from "react-router";
import {
  Package, Search, RefreshCw, Loader2, FileText, Upload,
} from "lucide-react";
import { listSkills, importSkill, type SkillIndexEntry } from "../../lib/skill-storage";

export default function SkillsPage() {
  const navigate = useNavigate();
  const [skills, setSkills] = useState<SkillIndexEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    listSkills().then((s) => { setSkills(s); setLoading(false); });
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
      else alert("Import failed. If the file has no YAML frontmatter, a name is required.");
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const filtered = skills.filter(
    (s) => !search || s.name.toLowerCase().includes(search.toLowerCase())
      || s.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">Skills</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">Reusable capabilities for your agents</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search skills..."
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none" />
          </div>
          <button onClick={refresh}
            className="p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
          <input ref={fileInputRef} type="file" accept=".md,.txt,.cursorrules" className="hidden" onChange={handleFileImport} />
          <button onClick={() => fileInputRef.current?.click()} disabled={importing}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">
            {importing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            Import
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
            <p className="text-sm font-medium text-gray-600 dark:text-gray-400">
              {search ? "No matching skills" : "No skills yet"}
            </p>
            <p className="text-xs mt-1">Create skills through the Meta Agent chat</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((skill) => (
              <button key={skill.id} onClick={() => navigate(`/skills/${skill.id}`)}
                className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 hover:border-blue-300 hover:shadow-sm transition-all text-left">
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-blue-500 flex-shrink-0" />
                  <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">{skill.name}</h3>
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
