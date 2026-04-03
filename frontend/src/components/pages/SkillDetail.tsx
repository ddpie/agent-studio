import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, useSearchParams, useBlocker } from "react-router";
import {
  Package, ChevronLeft, Trash2, Loader2, Save, GitCompare,
  FileText, FolderOpen, FolderClosed, File, ChevronRight as ChevronRightIcon,
  Plus, Pencil, FolderPlus,
} from "lucide-react";
import { getSkillContent, getSkillFile, listSkillFiles, deleteSkill, writeSkillFile, deleteSkillFile, renameSkillFile, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";
import Editor, { DiffEditor } from "@monaco-editor/react";
import { Tree, type NodeRendererProps } from "react-arborist";
import { useUISettings } from "../../stores/ui-settings-store";

// --- Language helpers ---

function getMonacoLanguage(filename: string): string {
  if (filename.endsWith(".py")) return "python";
  if (filename.endsWith(".md")) return "markdown";
  if (filename.endsWith(".json")) return "json";
  if (filename.endsWith(".js")) return "javascript";
  if (filename.endsWith(".ts")) return "typescript";
  if (filename.endsWith(".yaml") || filename.endsWith(".yml")) return "yaml";
  if (filename.endsWith(".sh")) return "shell";
  return "plaintext";
}

function useIsDark() {
  const { theme } = useUISettings();
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// --- Tree data helpers ---

type TreeNode = {
  id: string;
  name: string;
  children?: TreeNode[];
};

function buildTreeData(files: string[]): TreeNode[] {
  const root: TreeNode[] = [{ id: "SKILL.md", name: "SKILL.md" }];
  const dirMap = new Map<string, TreeNode>();

  for (const f of files) {
    const parts = f.split("/");
    if (parts.length === 1) {
      root.push({ id: f, name: f });
    } else {
      const dirName = parts[0];
      if (!dirMap.has(dirName)) {
        const dirNode: TreeNode = { id: `__dir__${dirName}`, name: dirName, children: [] };
        dirMap.set(dirName, dirNode);
        root.push(dirNode);
      }
      dirMap.get(dirName)!.children!.push({ id: f, name: parts.slice(1).join("/") });
    }
  }
  return root;
}

function getFileIcon(name: string, isFolder: boolean, isOpen: boolean) {
  if (isFolder) return isOpen ? <FolderOpen className="w-3.5 h-3.5 text-yellow-500" /> : <FolderClosed className="w-3.5 h-3.5 text-yellow-500" />;
  if (name.endsWith(".md")) return <FileText className="w-3.5 h-3.5 text-blue-400" />;
  if (name.endsWith(".py")) return <File className="w-3.5 h-3.5 text-green-400" />;
  if (name.endsWith(".json")) return <File className="w-3.5 h-3.5 text-yellow-400" />;
  if (name.endsWith(".js") || name.endsWith(".ts")) return <File className="w-3.5 h-3.5 text-amber-400" />;
  return <File className="w-3.5 h-3.5 text-gray-400" />;
}

// --- Diff Modal (Monaco DiffEditor) ---

function DiffModal({ changes, onClose, isDark }: {
  changes: Map<string, { original: string; edited: string }>;
  onClose: () => void;
  isDark: boolean;
}) {
  const entries = [...changes.entries()];
  const [activeIdx, setActiveIdx] = useState(0);
  if (entries.length === 0) return null;

  const [path, { original, edited }] = entries[activeIdx];

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl`} onClick={(e) => e.stopPropagation()}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
          <div className="flex items-center gap-3">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className={`text-sm font-semibold ${isDark ? "text-gray-200" : "text-gray-800"}`}>Changes</span>
            <div className="flex items-center gap-1">
              {entries.map(([p], i) => (
                <button key={p} onClick={() => setActiveIdx(i)}
                  className={`px-2 py-0.5 text-[11px] rounded ${i === activeIdx
                    ? "bg-blue-600 text-white"
                    : isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"
                  }`}>
                  {p}
                </button>
              ))}
            </div>
          </div>
          <button onClick={onClose} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-800" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Close</button>
        </div>
        <div className="flex-1 overflow-hidden">
          <DiffEditor
            original={original}
            modified={edited}
            language={getMonacoLanguage(path)}
            theme={isDark ? "vs-dark" : "light"}
            options={{
              readOnly: true,
              renderSideBySide: true,
              fontSize: 12,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
            }}
          />
        </div>
      </div>
    </div>
  );
}

// --- Main Component ---

export default function SkillDetail() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
  const isDark = useIsDark();
  const [skill, setSkill] = useState<SkillIndexEntry | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<string[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeFile = searchParams.get("file");
  const [loadingContent, setLoadingContent] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(224);
  const dragging = useRef(false);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startW = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      setSidebarWidth(Math.min(Math.max(startW + ev.clientX - startX, 120), 400));
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }, [sidebarWidth]);

  // Multi-file edit state
  const [originalContents] = useState(() => new Map<string, string>());
  const [editedContents] = useState(() => new Map<string, string>());
  const [changedFiles, setChangedFiles] = useState<Set<string>>(new Set());

  // Staging state — all file ops are local until Save
  const [pendingCreates] = useState(() => new Map<string, string>());
  const [pendingDeletes, setPendingDeletes] = useState<Set<string>>(new Set());
  const [pendingDeleteDirs, setPendingDeleteDirs] = useState<Set<string>>(new Set());
  const [pendingRenames] = useState(() => new Map<string, string>()); // oldPath → newPath

  const currentPath = activeFile || "SKILL.md";

  // Compute virtual file list: real files + renames + creates (keep deletes for strikethrough)
  const virtualFiles = useMemo(() => {
    let files = [...skillFiles];
    // Apply renames
    files = files.map(f => pendingRenames.get(f) ?? f);
    // Add created
    for (const path of pendingCreates.keys()) {
      if (!files.includes(path)) files.push(path);
    }
    return files;
  }, [skillFiles, pendingDeletes, pendingRenames, pendingCreates, changedFiles]); // changedFiles triggers re-render

  const treeData = useMemo(() => buildTreeData(virtualFiles), [virtualFiles]);

  const hasPendingOps = changedFiles.size > 0 || pendingCreates.size > 0 || pendingDeletes.size > 0 || pendingDeleteDirs.size > 0 || pendingRenames.size > 0;
  const pendingCount = changedFiles.size + pendingCreates.size + pendingDeletes.size + pendingDeleteDirs.size + pendingRenames.size;

  useEffect(() => {
    if (!skillId) return;
    originalContents.clear();
    editedContents.clear();
    pendingCreates.clear();
    pendingDeletes.clear();
    pendingRenames.clear();
    setChangedFiles(new Set());
    setPendingDeletes(new Set());
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
    // Check pending creates first
    if (pendingCreates.has(path)) {
      setSkillContent(editedContents.get(path) ?? pendingCreates.get(path)!);
      return;
    }
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
  }, [skillId, editedContents, originalContents, pendingCreates]);

  const handleNodeClick = async (nodeId: string) => {
    if (nodeId.startsWith("__dir__")) return;
    if (nodeId === currentPath) return;
    if (nodeId === "SKILL.md") {
      setSearchParams({});
    } else {
      setSearchParams({ file: nodeId });
    }
    await loadFile(nodeId);
  };

  const handleEditorChange = (val: string | undefined) => {
    if (val === undefined) return;
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

  const handleSaveAll = async () => {
    if (!skillId || !hasPendingOps) return;
    setSaving(true);

    // 1. Execute deletes (individual files)
    for (const path of pendingDeletes) {
      await deleteSkillFile(skillId, path);
    }

    // 1b. Execute directory deletes (all files under dir)
    for (const dir of pendingDeleteDirs) {
      const dirFiles = skillFiles.filter(f => f.startsWith(dir + "/"));
      for (const f of dirFiles) {
        await deleteSkillFile(skillId, f);
      }
    }

    // 2. Execute renames (copy + delete)
    for (const [oldPath, newPath] of pendingRenames) {
      if (!pendingDeletes.has(oldPath)) {
        await renameSkillFile(skillId, oldPath, newPath);
      }
    }

    // 3. Save created files
    for (const [path, content] of pendingCreates) {
      const edited = editedContents.get(path) ?? content;
      await writeSkillFile(skillId, path, edited);
    }

    // 4. Save edited existing files
    for (const path of changedFiles) {
      if (pendingCreates.has(path) || pendingDeletes.has(path)) continue;
      const content = editedContents.get(path);
      if (content === undefined) continue;
      // If renamed, save to new path
      const actualPath = pendingRenames.get(path) ?? path;
      await writeSkillFile(skillId, actualPath, content);
    }

    pendingCreates.clear();
    pendingDeletes.clear();
    pendingDeleteDirs.clear();
    pendingRenames.clear();
    editedContents.clear();
    originalContents.clear();
    setChangedFiles(new Set());
    setPendingDeletes(new Set());
    setPendingDeleteDirs(new Set());

    // Refresh from S3
    const [files, content] = await Promise.all([
      listSkillFiles(skillId),
      getSkillContent(skillId),
    ]);
    setSkillFiles(files);
    if (content !== null) {
      originalContents.set("SKILL.md", content);
      if (currentPath === "SKILL.md") setSkillContent(content);
      // Update header
      const { parseFrontmatter } = await import("../../lib/skill-storage");
      const meta = parseFrontmatter(content);
      if (meta && skill) setSkill({ ...skill, name: meta.name, description: meta.description });
    }
    // Reload current file if not SKILL.md
    if (currentPath !== "SKILL.md") {
      const fc = await getSkillFile(skillId, currentPath);
      if (fc !== null) {
        setSkillContent(fc);
        originalContents.set(currentPath, fc);
      }
    }
    setSaving(false);
  };

  const handleDiscard = () => {
    pendingCreates.clear();
    pendingDeletes.clear();
    pendingDeleteDirs.clear();
    pendingRenames.clear();
    editedContents.clear();
    setChangedFiles(new Set());
    setPendingDeletes(new Set());
    setPendingDeleteDirs(new Set());
    // Reload current file from original
    const orig = originalContents.get(currentPath);
    if (orig !== undefined) setSkillContent(orig);
  };

  const handleDelete = async () => {
    if (!skillId) return;
    await deleteSkill(skillId);
    navigate("/skills");
  };

  const getDiffChanges = (): Map<string, { original: string; edited: string }> => {
    const result = new Map<string, { original: string; edited: string }>();
    // Edited files
    for (const path of changedFiles) {
      if (pendingDeletes.has(path)) continue;
      const original = originalContents.get(path) ?? "";
      const edited = editedContents.get(path) ?? "";
      result.set(path, { original, edited });
    }
    // Deleted files — show as full removal
    for (const path of pendingDeletes) {
      const original = originalContents.get(path) ?? "";
      result.set(`${path} (deleted)`, { original, edited: "" });
    }
    // New files
    for (const [path, content] of pendingCreates) {
      const edited = editedContents.get(path) ?? content;
      result.set(`${path} (new)`, { original: "", edited });
    }
    // Renames
    for (const [oldPath, newPath] of pendingRenames) {
      if (!result.has(newPath) && !pendingDeletes.has(oldPath)) {
        result.set(`${oldPath} → ${newPath}`, { original: originalContents.get(newPath) ?? "", edited: editedContents.get(newPath) ?? originalContents.get(newPath) ?? "" });
      }
    }
    return result;
  };

  // --- File management ---
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string; isFolder: boolean } | null>(null);
  const [newFileDialog, setNewFileDialog] = useState<{ parentDir: string } | null>(null);
  const [newFolderDialog, setNewFolderDialog] = useState(false);
  const [newFolderParent, setNewFolderParent] = useState("");
  const [renameDialog, setRenameDialog] = useState<{ path: string; currentName: string } | null>(null);
  const [deleteFileDialog, setDeleteFileDialog] = useState<string | null>(null);
  const [dialogInput, setDialogInput] = useState("");

  const handleNewFile = async () => {
    if (!dialogInput.trim()) return;
    const dir = newFileDialog?.parentDir;
    const path = dir ? `${dir}/${dialogInput.trim()}` : dialogInput.trim();
    // Stage locally — don't write to S3
    pendingCreates.set(path, "");
    editedContents.set(path, "");
    setNewFileDialog(null);
    setDialogInput("");
    // Trigger re-render
    setChangedFiles(new Set([...changedFiles, path]));
    // Open the new file
    setSearchParams({ file: path });
    setSkillContent("");
  };

  const handleNewFolder = async () => {
    if (!dialogInput.trim()) return;
    const folderName = newFolderParent ? `${newFolderParent}/${dialogInput.trim()}` : dialogInput.trim();
    setNewFolderDialog(false);
    setNewFolderParent("");
    setNewFileDialog({ parentDir: folderName });
    setDialogInput("");
  };

  const handleDeleteFile = async () => {
    if (!deleteFileDialog) return;
    const target = deleteFileDialog;
    const isDir = target.startsWith("__dir__");
    const next = new Set(changedFiles);
    const nextDeletes = new Set(pendingDeletes);

    if (isDir) {
      const dirName = target.replace("__dir__", "");
      const nextDirs = new Set(pendingDeleteDirs);
      nextDirs.add(dirName);
      setPendingDeleteDirs(nextDirs);
      // Clean up edit state for files in this dir
      for (const f of [...skillFiles, ...pendingCreates.keys()]) {
        if (f.startsWith(dirName + "/")) {
          editedContents.delete(f);
          next.delete(f);
        }
      }
      if (currentPath.startsWith(dirName + "/")) {
        setSearchParams({});
        await loadFile("SKILL.md");
      }
    } else {
      if (pendingCreates.has(target)) {
        // Just remove from pending — never existed on S3
        pendingCreates.delete(target);
        editedContents.delete(target);
        next.delete(target);
      } else {
        // Mark for deletion on save
        nextDeletes.add(target);
        editedContents.delete(target);
        originalContents.delete(target);
        next.delete(target);
      }
      if (target === currentPath) {
        setSearchParams({});
        await loadFile("SKILL.md");
      }
    }
    setChangedFiles(next);
    setPendingDeletes(nextDeletes);
    setDeleteFileDialog(null);
  };

  const handleRename = async () => {
    if (!renameDialog || !dialogInput.trim()) return;
    const oldPath = renameDialog.path;
    const parts = oldPath.split("/");
    parts[parts.length - 1] = dialogInput.trim();
    const newPath = parts.join("/");
    if (newPath === oldPath) { setRenameDialog(null); return; }

    if (pendingCreates.has(oldPath)) {
      // Rename a pending create — just move in memory
      const content = editedContents.get(oldPath) ?? pendingCreates.get(oldPath) ?? "";
      pendingCreates.delete(oldPath);
      pendingCreates.set(newPath, content);
      if (editedContents.has(oldPath)) {
        editedContents.set(newPath, editedContents.get(oldPath)!);
        editedContents.delete(oldPath);
      }
    } else {
      // Stage rename for existing file
      pendingRenames.set(oldPath, newPath);
      if (editedContents.has(oldPath)) {
        editedContents.set(newPath, editedContents.get(oldPath)!);
        editedContents.delete(oldPath);
      }
      if (originalContents.has(oldPath)) {
        originalContents.set(newPath, originalContents.get(oldPath)!);
        originalContents.delete(oldPath);
      }
    }

    const next = new Set(changedFiles);
    next.delete(oldPath);
    next.add(newPath);
    setChangedFiles(next);

    if (oldPath === currentPath) {
      setSearchParams({ file: newPath });
      const content = editedContents.get(newPath) ?? originalContents.get(newPath) ?? "";
      setSkillContent(content);
    }
    setRenameDialog(null);
    setDialogInput("");
  };

  const handleContextMenu = (e: React.MouseEvent, nodeId: string, isFolder: boolean) => {
    e.preventDefault();
    e.stopPropagation();
    if (nodeId === "SKILL.md") return; // Can't delete/rename SKILL.md
    setContextMenu({ x: e.clientX, y: e.clientY, nodeId, isFolder });
  };

  // Close context menu on click anywhere
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [contextMenu]);

  // Warn on browser navigation with unsaved changes
  useEffect(() => {
    if (changedFiles.size === 0) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [changedFiles.size]);

  // Sidebar collapse
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Block route navigation when there are unsaved changes
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (!hasPendingOps) return false;
    // Only block if leaving the skill detail page, not when switching files (searchParams)
    return currentLocation.pathname !== nextLocation.pathname;
  });

  // Custom tree node renderer
  const FileNode = useCallback(({ node, style }: NodeRendererProps<TreeNode>) => {
    const isFolder = node.isInternal;
    const isActive = !isFolder && node.id === currentPath;
    const isChanged = changedFiles.has(node.id);
    const isDeleted = pendingDeletes.has(node.id) || (isFolder && pendingDeleteDirs.has(node.id.replace("__dir__", "")));
    const isNew = pendingCreates.has(node.id);
    const canContextMenu = node.id !== "SKILL.md";

    return (
      <div
        style={style}
        className={`group flex items-center gap-1.5 px-2 py-0.5 cursor-pointer select-none text-xs rounded-sm mx-1
          ${isDeleted
            ? isDark ? "text-gray-600 line-through" : "text-gray-400 line-through"
            : isActive
              ? isDark ? "bg-blue-600/20 text-blue-400" : "bg-blue-100 text-blue-700"
              : isNew
                ? isDark ? "text-green-400" : "text-green-600"
                : isDark ? "text-gray-300 hover:bg-gray-700/50" : "text-gray-700 hover:bg-gray-200/50"
          }`}
        onClick={() => {
          if (isDeleted) return;
          if (isFolder) {
            node.toggle();
          } else {
            handleNodeClick(node.id);
          }
        }}
        onContextMenu={canContextMenu && !isDeleted ? (e) => handleContextMenu(e, node.id, isFolder) : undefined}
      >
        {isFolder && (
          <ChevronRightIcon className={`w-3 h-3 ${isDark ? "text-gray-500" : "text-gray-400"} transition-transform ${node.isOpen ? "rotate-90" : ""}`} />
        )}
        {getFileIcon(node.data.name, isFolder, node.isOpen)}
        <span className="truncate flex-1">{node.data.name}</span>
        {isNew && <span className="text-[9px] text-green-500 font-medium">NEW</span>}
        {isDeleted && <span className="text-[9px] text-red-400 font-medium">DEL</span>}
        {isChanged && !isDeleted && !isNew && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />}
      </div>
    );
  }, [currentPath, changedFiles, isDark, pendingDeletes, pendingCreates]);

  if (!skill && !loadingContent) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400">
        Skill not found
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <button onClick={() => navigate("/skills")} className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded text-gray-600 dark:text-gray-300">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <Package className="w-4 h-4 text-blue-500" />
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{skill?.name}</h2>
        {skill?.description && (
          <span className="text-xs text-gray-400 ml-2 truncate">{skill.description}</span>
        )}
        <div className="flex-1" />
        {hasPendingOps && (
          <button onClick={() => setShowDiff(true)}
            className="flex items-center gap-1 px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-lg transition-colors">
            <GitCompare className="w-3.5 h-3.5" />
            Diff ({pendingCount})
          </button>
        )}
        {hasPendingOps && (
          <button onClick={handleDiscard}
            className="px-2.5 py-1.5 text-[12px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors">
            Discard
          </button>
        )}
        {hasPendingOps && (
          <button onClick={handleSaveAll} disabled={saving}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Save ({pendingCount})
          </button>
        )}
        <button onClick={() => setShowDeleteConfirm(true)} className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar: react-arborist file tree */}
        {!sidebarCollapsed && (
          <div style={{ width: sidebarWidth }} className={`border-r overflow-hidden flex-shrink-0 flex flex-col ${isDark ? "border-gray-700 bg-[#252526]" : "border-gray-200 bg-gray-50"}`}>
            <div className={`px-3 py-2 flex items-center justify-between ${isDark ? "text-gray-500" : "text-gray-400"}`}>
              <span className="text-[10px] font-semibold uppercase tracking-wider">Files</span>
              <div className="flex items-center gap-0.5">
                <button onClick={() => { setNewFileDialog({ parentDir: "" }); setDialogInput(""); }}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title="New file">
                  <Plus className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => { setNewFolderDialog(true); setNewFolderParent(""); setDialogInput(""); }}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title="New folder">
                  <FolderPlus className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => setSidebarCollapsed(true)}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title="Hide sidebar">
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          <div className="flex-1 overflow-auto">
            <Tree
              data={treeData}
              openByDefault
              width={sidebarWidth}
              rowHeight={28}
              indent={16}
              disableDrag
              disableDrop
              disableEdit
            >
              {FileNode}
            </Tree>
          </div>
        </div>
        )}
        {sidebarCollapsed && (
          <button onClick={() => setSidebarCollapsed(false)}
            className={`flex-shrink-0 px-1 py-4 border-r ${isDark ? "border-gray-700 bg-[#252526] text-gray-500 hover:text-gray-300" : "border-gray-200 bg-gray-50 text-gray-400 hover:text-gray-600"}`}
            title="Show sidebar">
            <ChevronRightIcon className="w-3.5 h-3.5" />
          </button>
        )}
        {/* Drag handle */}
        {!sidebarCollapsed && (
          <div
            onMouseDown={onDragStart}
            className="w-1 cursor-col-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 flex-shrink-0 transition-colors"
            title="Drag to resize"
          />
        )}

        {/* Content: Monaco Editor */}
        <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
          {loadingContent ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
            </div>
          ) : skillContent !== null ? (
            <div className="flex flex-col flex-1 overflow-hidden">
              <div className={`flex items-center justify-between px-3 py-1.5 border-b ${isDark ? "bg-gray-800 border-gray-700" : "bg-gray-100 border-gray-200"}`}>
                <span className={`text-xs font-mono ${isDark ? "text-gray-300" : "text-gray-600"}`}>{activeFile || "SKILL.md"}</span>
              </div>
              <div className="flex-1">
                <Editor
                  value={skillContent}
                  onChange={handleEditorChange}
                  language={getMonacoLanguage(currentPath)}
                  theme={isDark ? "vs-dark" : "light"}
                  options={{
                    fontSize: 12,
                    minimap: { enabled: true },
                    scrollBeyondLastLine: false,
                    wordWrap: currentPath.endsWith(".md") ? "on" : "off",
                    lineNumbers: "on",
                    folding: true,
                    automaticLayout: true,
                    tabSize: 2,
                  }}
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400 p-6">Failed to load content.</p>
          )}
        </div>
      </div>

      {showDiff && <DiffModal changes={getDiffChanges()} onClose={() => setShowDiff(false)} isDark={isDark} />}

      {/* Unsaved changes blocker */}
      {blocker.state === "blocked" && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center">
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>Unsaved changes</p>
            <p className="text-xs text-gray-500 mb-4">
              You have {changedFiles.size} unsaved file{changedFiles.size > 1 ? "s" : ""}. Leaving will discard your changes.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => blocker.reset?.()} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Stay</button>
              <button onClick={() => blocker.proceed?.()} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">Discard & Leave</button>
            </div>
          </div>
        </div>
      )}

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setShowDeleteConfirm(false)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>Delete skill?</p>
            <p className="text-xs text-gray-500 mb-4">
              Remove <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{skill?.name}</span> and all its files. This cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowDeleteConfirm(false)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleDelete} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">Delete</button>
            </div>
          </div>
        </div>
      )}

      {/* Context menu */}
      {contextMenu && (
        <div
          style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, zIndex: 60 }}
          className={`${isDark ? "bg-gray-800 border-gray-700" : "bg-white border-gray-200"} border rounded-lg shadow-xl py-1 min-w-[140px]`}
        >
          {contextMenu.isFolder && (
            <>
              <button onClick={() => { setNewFileDialog({ parentDir: contextMenu.nodeId.replace("__dir__", "") }); setDialogInput(""); setContextMenu(null); }}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
                <Plus className="w-3 h-3" /> New file here
              </button>
              <button onClick={() => {
                const parent = contextMenu.nodeId.replace("__dir__", "");
                setNewFolderParent(parent);
                setNewFolderDialog(true);
                setDialogInput("");
                setContextMenu(null);
              }}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
                <FolderPlus className="w-3 h-3" /> New folder here
              </button>
            </>
          )}
          {!contextMenu.isFolder && (
            <button onClick={() => { setRenameDialog({ path: contextMenu.nodeId, currentName: contextMenu.nodeId.split("/").pop()! }); setDialogInput(contextMenu.nodeId.split("/").pop()!); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <Pencil className="w-3 h-3" /> Rename
            </button>
          )}
          <button onClick={() => { setDeleteFileDialog(contextMenu.nodeId); setContextMenu(null); }}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 text-red-400 ${isDark ? "hover:bg-gray-700" : "hover:bg-red-50"}`}>
            <Trash2 className="w-3 h-3" /> Delete
          </button>
        </div>
      )}

      {/* New file dialog */}
      {newFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setNewFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>
              New file{newFileDialog.parentDir ? ` in ${newFileDialog.parentDir}/` : ""}
            </p>
            <input
              autoFocus
              value={dialogInput}
              onChange={e => setDialogInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleNewFile(); if (e.key === "Escape") setNewFileDialog(null); }}
              placeholder="filename.py"
              className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setNewFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleNewFile} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">Create</button>
            </div>
          </div>
        </div>
      )}

      {/* New folder dialog */}
      {newFolderDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setNewFolderDialog(false)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>New folder</p>
            <input
              autoFocus
              value={dialogInput}
              onChange={e => setDialogInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleNewFolder(); if (e.key === "Escape") setNewFolderDialog(false); }}
              placeholder="folder-name"
              className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setNewFolderDialog(false)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleNewFolder} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">Create</button>
            </div>
          </div>
        </div>
      )}

      {/* Rename dialog */}
      {renameDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setRenameDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>Rename file</p>
            <input
              autoFocus
              value={dialogInput}
              onChange={e => setDialogInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenameDialog(null); }}
              className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setRenameDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleRename} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">Rename</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete file confirm */}
      {deleteFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setDeleteFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>Delete file?</p>
            <p className="text-xs text-gray-500 mb-4">
              Remove <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{deleteFileDialog}</span>. You can undo this with Discard before saving.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeleteFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleDeleteFile} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
