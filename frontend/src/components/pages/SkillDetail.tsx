import { useEffect, useState, useMemo, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router";
import {
  Package, ChevronLeft, FileText, Trash2, Loader2, FolderOpen, File, Save, GitCompare,
} from "lucide-react";
import { getSkillContent, getSkillFile, listSkillFiles, deleteSkill, writeSkillFile, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";
import { createPatch } from "diff";
import CodeMirror from "@uiw/react-codemirror";
import { vscodeDark } from "@uiw/codemirror-theme-vscode";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { json } from "@codemirror/lang-json";
import { javascript } from "@codemirror/lang-javascript";

function getLanguageExtension(filename: string) {
  if (filename.endsWith(".py")) return [python()];
  if (filename.endsWith(".md")) return [markdown()];
  if (filename.endsWith(".json")) return [json()];
  if (filename.endsWith(".js") || filename.endsWith(".ts")) return [javascript()];
  return [];
}

function DiffModal({ changes, onClose }: {
  changes: Map<string, { original: string; edited: string }>;
  onClose: () => void;
}) {
  const entries = [...changes.entries()];
  if (entries.length === 0) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className="bg-white dark:bg-gray-900 rounded-xl w-[70vw] max-h-[80vh] flex flex-col shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center gap-2">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">Changes ({entries.length} file{entries.length > 1 ? "s" : ""})</span>
          </div>
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg">Close</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {entries.map(([path, { original, edited }]) => {
            const patch = createPatch(path, original, edited, "", "", { context: 8 });
            const lines = patch.split("\n").slice(4);
            return (
              <div key={path} className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="px-3 py-1.5 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 text-xs font-semibold text-gray-600 dark:text-gray-300">{path}</div>
                <pre className="text-[11px] font-mono leading-relaxed overflow-x-auto p-3 bg-gray-900 text-gray-300">
                  {lines.map((line, i) => {
                    const color = line.startsWith("+") ? "text-green-400" : line.startsWith("-") ? "text-red-400" : line.startsWith("@@") ? "text-blue-400" : "text-gray-500";
                    return <div key={i} className={color}>{line || " "}</div>;
                  })}
                </pre>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export default function SkillDetail() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
  const [skill, setSkill] = useState<SkillIndexEntry | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<string[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeFile = searchParams.get("file");
  const [loadingContent, setLoadingContent] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  // Multi-file edit state
  const [originalContents] = useState(() => new Map<string, string>());
  const [editedContents] = useState(() => new Map<string, string>());
  const [changedFiles, setChangedFiles] = useState<Set<string>>(new Set());

  const currentPath = activeFile || "SKILL.md";
  const extensions = useMemo(() => getLanguageExtension(currentPath), [currentPath]);
  const currentEdited = editedContents.get(currentPath);
  const hasCurrentChanges = currentEdited !== undefined && currentEdited !== originalContents.get(currentPath);

  useEffect(() => {
    if (!skillId) return;
    originalContents.clear();
    editedContents.clear();
    setChangedFiles(new Set());
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
      if (content !== null) {
        originalContents.set(fileParam || "SKILL.md", content);
      }
      setLoadingContent(false);
    });
  }, [skillId]);

  const loadFile = useCallback(async (path: string) => {
    if (!skillId) return;
    if (editedContents.has(path)) {
      setSkillContent(editedContents.get(path)!);
      return;
    }
    setLoadingContent(true);
    const content = path === "SKILL.md"
      ? await getSkillContent(skillId)
      : await getSkillFile(skillId, path);
    setSkillContent(content);
    if (content !== null) {
      originalContents.set(path, content);
    }
    setLoadingContent(false);
  }, [skillId, editedContents, originalContents]);

  const handleFileClick = async (path: string) => {
    if (path === currentPath) return;
    setSearchParams({ file: path });
    await loadFile(path);
  };

  const handleBackToSkillMd = async () => {
    if (currentPath === "SKILL.md") return;
    setSearchParams({});
    await loadFile("SKILL.md");
  };

  const handleEditorChange = (val: string) => {
    editedContents.set(currentPath, val);
    setSkillContent(val);
    const original = originalContents.get(currentPath);
    const next = new Set(changedFiles);
    if (original !== undefined && val !== original) {
      next.add(currentPath);
    } else {
      next.delete(currentPath);
    }
    setChangedFiles(next);
  };

  const handleSave = async () => {
    if (!skillId || !hasCurrentChanges || currentEdited === undefined) return;
    setSaving(true);
    const ok = await writeSkillFile(skillId, currentPath, currentEdited);
    if (ok) {
      originalContents.set(currentPath, currentEdited);
      editedContents.delete(currentPath);
      const next = new Set(changedFiles);
      next.delete(currentPath);
      setChangedFiles(next);
    }
    setSaving(false);
  };

  const handleDelete = async () => {
    if (!skillId || !confirm("Delete this skill?")) return;
    await deleteSkill(skillId);
    navigate("/skills");
  };

  const getDiffChanges = (): Map<string, { original: string; edited: string }> => {
    const result = new Map<string, { original: string; edited: string }>();
    for (const path of changedFiles) {
      const original = originalContents.get(path) ?? "";
      const edited = editedContents.get(path) ?? "";
      result.set(path, { original, edited });
    }
    return result;
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
      {/* Header */}
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
        {changedFiles.size > 0 && (
          <button onClick={() => setShowDiff(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-colors">
            <GitCompare className="w-3.5 h-3.5" />
            Diff
          </button>
        )}
        {hasCurrentChanges && (
          <button onClick={handleSave} disabled={saving}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Save
          </button>
        )}
        <button onClick={handleDelete} className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar: always show */}
        <div className="w-56 border-r border-gray-200 dark:border-gray-700 overflow-y-auto py-3 flex-shrink-0">
          <button onClick={handleBackToSkillMd}
            className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${!activeFile ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 font-medium" : "text-gray-700 dark:text-gray-300"}`}>
            <FileText className="w-3.5 h-3.5 flex-shrink-0" />
            SKILL.md
            {changedFiles.has("SKILL.md") && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0 ml-auto" />}
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
                      className={`w-full flex items-center gap-2 pl-8 pr-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${activeFile === fullPath ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 font-medium" : "text-gray-600 dark:text-gray-400"}`}>
                      <File className="w-3 h-3 flex-shrink-0" /> {f}
                      {changedFiles.has(fullPath) && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0 ml-auto" />}
                    </button>
                  );
                })}
              </div>
            ) : files.map((f) => (
              <button key={f} onClick={() => handleFileClick(f)}
                className={`w-full flex items-center gap-2 px-4 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800 ${activeFile === f ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 font-medium" : "text-gray-600 dark:text-gray-400"}`}>
                <File className="w-3.5 h-3.5 flex-shrink-0" /> {f}
                {changedFiles.has(f) && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0 ml-auto" />}
              </button>
            ))
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {loadingContent ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
            </div>
          ) : skillContent !== null ? (
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700">
                <span className="text-xs font-mono text-gray-300">{activeFile || "SKILL.md"}</span>
              </div>
              <CodeMirror
                value={skillContent}
                onChange={handleEditorChange}
                theme={vscodeDark}
                extensions={extensions}
                maxHeight="calc(100vh - 240px)"
                style={{ fontSize: "12px" }}
                basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
              />
            </div>
          ) : (
            <p className="text-sm text-gray-400">Failed to load content.</p>
          )}
        </div>
      </div>

      {showDiff && <DiffModal changes={getDiffChanges()} onClose={() => setShowDiff(false)} />}
    </div>
  );
}
