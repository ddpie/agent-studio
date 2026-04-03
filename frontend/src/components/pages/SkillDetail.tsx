import { useEffect, useState } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router";
import {
  Package, ChevronLeft, FileText, Trash2, Loader2, FolderOpen, File, ChevronRight,
} from "lucide-react";
import { getSkillContent, getSkillFile, listSkillFiles, deleteSkill, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";

export default function SkillDetail() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
  const [skill, setSkill] = useState<SkillIndexEntry | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<string[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeFile = searchParams.get("file");
  const [loadingContent, setLoadingContent] = useState(true);

  useEffect(() => {
    if (!skillId) return;
    setLoadingContent(true);
    const fileParam = searchParams.get("file");
    Promise.all([
      listSkills(),
      fileParam ? getSkillFile(skillId, fileParam) : getSkillContent(skillId),
      listSkillFiles(skillId),
    ]).then(([skills, content, files]) => {
      const found = skills.find(s => s.id === skillId) || null;
      setSkill(found);
      setSkillContent(content);
      setSkillFiles(files);
      setLoadingContent(false);
    });
  }, [skillId]);

  const handleFileClick = async (path: string) => {
    if (!skillId) return;
    setSearchParams({ file: path });
    setLoadingContent(true);
    const content = await getSkillFile(skillId, path);
    setSkillContent(content);
    setLoadingContent(false);
  };

  const handleBackToSkillMd = async () => {
    if (!skillId) return;
    setSearchParams({});
    setLoadingContent(true);
    const content = await getSkillContent(skillId);
    setSkillContent(content);
    setLoadingContent(false);
  };

  const handleDelete = async () => {
    if (!skillId || !confirm("Delete this skill?")) return;
    await deleteSkill(skillId);
    navigate("/skills");
  };

  if (!skill && !loadingContent) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        Skill not found
      </div>
    );
  }

  // Group files by directory
  const dirs = new Map<string, string[]>();
  for (const f of skillFiles) {
    const slash = f.indexOf("/");
    if (slash > 0) {
      const dir = f.slice(0, slash);
      const rest = f.slice(slash + 1);
      if (!dirs.has(dir)) dirs.set(dir, []);
      dirs.get(dir)!.push(rest);
    } else {
      if (!dirs.has("")) dirs.set("", []);
      dirs.get("")!.push(f);
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <button onClick={() => navigate("/skills")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <Package className="w-4 h-4 text-blue-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{skill?.name}</h2>
        {skill?.description && (
          <span className="text-xs text-gray-400 ml-2 truncate">{skill.description}</span>
        )}
        <div className="flex-1" />
        <button onClick={handleDelete} className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
      <div className="flex flex-1 overflow-hidden">
        {skillFiles.length > 0 && (
          <div className="w-56 border-r border-gray-200 dark:border-gray-700 overflow-y-auto py-3 flex-shrink-0">
            <button onClick={handleBackToSkillMd}
              className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${!activeFile ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-700 dark:text-gray-300"}`}>
              <FileText className="w-3.5 h-3.5 flex-shrink-0" /> SKILL.md
            </button>
            {[...dirs.entries()].map(([dir, files]) =>
              dir ? (
                <div key={dir} className="mt-2">
                  <div className="flex items-center gap-1.5 px-4 py-1 text-[11px] text-gray-400 uppercase tracking-wide">
                    <FolderOpen className="w-3 h-3" /> {dir}
                  </div>
                  {files.map((f) => {
                    const fullPath = `${dir}/${f}`;
                    return (
                      <button key={fullPath} onClick={() => handleFileClick(fullPath)}
                        className={`w-full flex items-center gap-2 pl-8 pr-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${activeFile === fullPath ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 dark:text-gray-400"}`}>
                        <File className="w-3 h-3 flex-shrink-0" /> {f}
                      </button>
                    );
                  })}
                </div>
              ) : files.map((f) => (
                <button key={f} onClick={() => handleFileClick(f)}
                  className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${activeFile === f ? "bg-blue-50 text-blue-700 font-medium" : "text-gray-600 dark:text-gray-400"}`}>
                  <File className="w-3.5 h-3.5 flex-shrink-0" /> {f}
                </button>
              ))
            )}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-6">
          {activeFile && (
            <div className="flex items-center gap-1 text-xs text-gray-400 mb-3">
              <span>{skill?.name}</span>
              <ChevronRight className="w-3 h-3" />
              <span className="text-gray-600 dark:text-gray-400">{activeFile}</span>
            </div>
          )}
          {loadingContent ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
            </div>
          ) : skillContent ? (
            <pre className="text-xs text-gray-700 dark:text-gray-300 whitespace-pre-wrap font-mono leading-relaxed">{skillContent}</pre>
          ) : (
            <p className="text-sm text-gray-400">Failed to load content.</p>
          )}
        </div>
      </div>
    </div>
  );
}
