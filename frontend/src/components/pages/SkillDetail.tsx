import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, useSearchParams, useBlocker } from "react-router";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft, Trash2, Loader2, Save, GitCompare,
  FileText, FolderOpen, FolderClosed, File, ChevronRight as ChevronRightIcon,
  Plus, Pencil, FolderPlus, ArrowRightLeft, Sparkles, ShieldCheck, Play,
} from "lucide-react";
import { deleteSkill, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";
import { useSkillStorage } from "../../hooks/useSkillStorage";
import Editor, { DiffEditor } from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { Tree, type NodeRendererProps } from "react-arborist";
import { useUISettings } from "../../stores/ui-settings-store";
import { useSkillAssistantStore } from "../../stores/skill-assistant-store";
import SkillAssistant from "../skills/SkillAssistant";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { preloadPyodide, checkPythonSyntax, isPyodideReady } from "../../lib/pyodide-checker";

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

/** Python validation using Pyodide compile() if available, fallback to basic checks */
function validatePython(code: string): { line: number; col: number; message: string; severity: number }[] {
  // Use Pyodide (real CPython compile) if loaded
  if (isPyodideReady()) {
    return checkPythonSyntax(code).map(e => ({
      line: e.line,
      col: e.col || 1,
      message: e.msg,
      severity: 8,
    }));
  }
  // Fallback: basic bracket balance check
  const markers: { line: number; col: number; message: string; severity: number }[] = [];
  const lines = code.split("\n");
  let parens = 0, brackets = 0, braces = 0;
  for (const line of lines) {
    for (const ch of line) {
      if (ch === "(") parens++; else if (ch === ")") parens--;
      else if (ch === "[") brackets++; else if (ch === "]") brackets--;
      else if (ch === "{") braces++; else if (ch === "}") braces--;
    }
  }
  if (parens !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced parentheses", severity: 8 });
  if (brackets !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced brackets", severity: 8 });
  if (braces !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced braces", severity: 8 });
  return markers;
}

/** Shell script validation: quotes, brackets, common issues */
function validateShell(code: string): { line: number; col: number; message: string; severity: number }[] {
  const markers: { line: number; col: number; message: string; severity: number }[] = [];
  const lines = code.split("\n");

  // Quote balance (single and double)
  let inSingle = false, inDouble = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    for (let j = 0; j < line.length; j++) {
      const ch = line[j];
      if (ch === "\\" && !inSingle) { j++; continue; } // skip escaped
      if (ch === "'" && !inDouble) inSingle = !inSingle;
      else if (ch === '"' && !inSingle) inDouble = !inDouble;
    }
  }
  if (inSingle) markers.push({ line: lines.length, col: 1, message: "Unterminated single quote", severity: 8 });
  if (inDouble) markers.push({ line: lines.length, col: 1, message: "Unterminated double quote", severity: 8 });

  // if/then/fi, do/done, case/esac balance
  let ifCount = 0, fiCount = 0, doCount = 0, doneCount = 0, caseCount = 0, esacCount = 0;
  for (const line of lines) {
    const words = line.trim().replace(/#.*$/, "").split(/\s+|;/);
    for (const w of words) {
      if (w === "if" || w === "elif") ifCount++;
      else if (w === "fi") fiCount++;
      else if (w === "do") doCount++;
      else if (w === "done") doneCount++;
      else if (w === "case") caseCount++;
      else if (w === "esac") esacCount++;
    }
  }
  if (ifCount !== fiCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced if/fi (${ifCount} if vs ${fiCount} fi)`, severity: 8 });
  if (doCount !== doneCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced do/done (${doCount} do vs ${doneCount} done)`, severity: 8 });
  if (caseCount !== esacCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced case/esac`, severity: 8 });

  return markers;
}

function useIsDark() {
  const { theme } = useUISettings();
  if (theme === "dark") return true;
  if (theme === "light") return false;
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

// --- Skill validation ---

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

function validateSkill(
  skillMdContent: string,
  virtualFiles: string[],
  pendingDeletes: Set<string>,
): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  // 1. Check frontmatter exists
  if (!skillMdContent.startsWith("---")) {
    errors.push("SKILL.md must start with YAML frontmatter (---)");
    return { valid: false, errors, warnings };
  }
  const parts = skillMdContent.split("---", 3);
  if (parts.length < 3) {
    errors.push("SKILL.md frontmatter is incomplete (missing closing ---)");
    return { valid: false, errors, warnings };
  }

  // 2. Parse frontmatter fields
  const fm = parts[1].trim();
  const fields: Record<string, string> = {};
  for (const line of fm.split("\n")) {
    const match = line.match(/^(\w[\w-]*):\s*(.*)/);
    if (match) fields[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
  }

  if (!fields.name) errors.push("Missing required field: name");
  if (!fields.description) warnings.push("Missing field: description (recommended)");
  if (fields.name && !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(fields.name)) {
    warnings.push("Skill name should be alphanumeric with hyphens/underscores");
  }

  // 3. Check files referenced in frontmatter
  if (fields.files || fm.includes("files:")) {
    const fileLines = fm.split("\n").filter(l => l.trim().startsWith("- "));
    for (const fl of fileLines) {
      const ref = fl.trim().replace(/^-\s*/, "").trim();
      if (ref && !virtualFiles.includes(ref) || pendingDeletes.has(ref)) {
        errors.push(`Referenced file not found: ${ref}`);
      }
    }
  }

  // 4. Check body is not empty
  const body = parts[2].trim();
  if (!body) warnings.push("SKILL.md body is empty");

  return { valid: errors.length === 0, errors, warnings };
}

// --- Tree data helpers ---

type TreeNode = {
  id: string;
  name: string;
  children?: TreeNode[];
};

function buildTreeData(files: string[]): TreeNode[] {
  const root: TreeNode[] = [{ id: "SKILL.md", name: "SKILL.md" }];

  // Build a nested map: each level maps name → { files, subdirs }
  interface DirEntry { children: Map<string, DirEntry>; files: { id: string; name: string }[] }
  const rootDir: DirEntry = { children: new Map(), files: [] };

  for (const f of files) {
    const parts = f.split("/");
    if (parts.length === 1) {
      rootDir.files.push({ id: f, name: f });
    } else {
      let current = rootDir;
      for (let i = 0; i < parts.length - 1; i++) {
        if (!current.children.has(parts[i])) {
          current.children.set(parts[i], { children: new Map(), files: [] });
        }
        current = current.children.get(parts[i])!;
      }
      current.files.push({ id: f, name: parts[parts.length - 1] });
    }
  }

  function buildLevel(dir: DirEntry, prefix: string): TreeNode[] {
    const nodes: TreeNode[] = [];
    // Subdirectories first
    for (const [name, sub] of [...dir.children.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      const dirPath = prefix ? `${prefix}/${name}` : name;
      nodes.push({
        id: `__dir__${dirPath}`,
        name,
        children: buildLevel(sub, dirPath),
      });
    }
    // Then files
    for (const f of dir.files.sort((a, b) => a.name.localeCompare(b.name))) {
      nodes.push({ id: f.id, name: f.name });
    }
    return nodes;
  }

  root.push(...buildLevel(rootDir, ""));
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
  const { t } = useTranslation();
  const entries = [...changes.entries()];
  const [activeIdx, setActiveIdx] = useState(0);

  // ESC to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (entries.length === 0) return null;

  const [path, { original, edited }] = entries[activeIdx];

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center animate-[fadeSlideIn_0.15s_ease-out]" onClick={onClose}>
      <div className={`${isDark ? "bg-gray-900" : "bg-white"} rounded-xl w-[85vw] h-[80vh] flex flex-col shadow-2xl`} onClick={(e) => e.stopPropagation()}>
        <div className={`flex items-center justify-between px-4 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
          <div className="flex items-center gap-3">
            <GitCompare className="w-4 h-4 text-blue-600" />
            <span className={`text-sm font-semibold ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.changes")}</span>
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
  const { t } = useTranslation();
  const [skill, setSkill] = useState<SkillIndexEntry | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [skillFiles, setSkillFiles] = useState<string[]>([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const agentId = searchParams.get("agentId");
  const agentSkillId = searchParams.get("agentSkillId");
  const storage = useSkillStorage(skillId!, agentId, agentSkillId);
  const activeFile = searchParams.get("file");
  const [loadingContent, setLoadingContent] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(224);
  const dragging = useRef(false);
  const [runOutput, setRunOutput] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

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

  /** Shared helper: stage a file move/rename. Handles chained renames correctly. */
  const stageMove = useCallback((oldPath: string, newPath: string) => {
    if (newPath === oldPath) return;

    if (pendingCreates.has(oldPath)) {
      // Move a pending create — just relocate in memory
      const content = editedContents.get(oldPath) ?? pendingCreates.get(oldPath) ?? "";
      pendingCreates.delete(oldPath);
      pendingCreates.set(newPath, content);
      if (editedContents.has(oldPath)) {
        editedContents.set(newPath, editedContents.get(oldPath)!);
        editedContents.delete(oldPath);
      }
    } else {
      // Check if oldPath is already the TARGET of an existing rename (chained move)
      let originalKey: string | null = null;
      for (const [k, v] of pendingRenames) {
        if (v === oldPath) { originalKey = k; break; }
      }

      if (originalKey !== null) {
        // Update the existing rename chain
        if (newPath === originalKey) {
          // Moved back to original location — cancel the rename
          pendingRenames.delete(originalKey);
        } else {
          pendingRenames.set(originalKey, newPath);
        }
      } else {
        // New rename
        pendingRenames.set(oldPath, newPath);
      }

      // Migrate edit/original contents
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
  }, [currentPath, changedFiles, editedContents, originalContents, pendingCreates, pendingRenames]);

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
      fileParam ? storage.getFile(fileParam) : storage.getContent(),
      storage.listFiles(),
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
    // Auto-open AI assistant
    useSkillAssistantStore.getState().openPanel(skillId);
    // Warm up Pyodide for Python syntax checking
    preloadPyodide();
  }, [skillId, storage]);

  const loadingPathRef = useRef<string | null>(null);

  const loadFile = useCallback(async (path: string) => {
    if (!skillId) return;
    if (pendingCreates.has(path)) {
      setSkillContent(editedContents.get(path) ?? pendingCreates.get(path)!);
      return;
    }
    if (editedContents.has(path)) {
      setSkillContent(editedContents.get(path)!);
      return;
    }
    loadingPathRef.current = path;
    setLoadingContent(true);
    const content = path === "SKILL.md"
      ? await storage.getContent()
      : await storage.getFile(path);
    // Only apply if this is still the file we're loading (prevents race condition)
    if (loadingPathRef.current !== path) return;
    setSkillContent(content);
    if (content !== null) {
      originalContents.set(path, content);
    }
    setLoadingContent(false);
  }, [skillId, editedContents, originalContents, pendingCreates, storage]);

  const handleNodeClick = async (nodeId: string) => {
    if (nodeId.startsWith("__dir__")) return;
    if (nodeId === currentPath) return;
    setValidationResult(null); // Clear old validation
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

    // Run Python/Shell validation on change (onValidate only fires for JSON/JS/TS)
    requestAnimationFrame(() => {
      const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
      if (!monacoInstance) return;
      const models = monacoInstance.editor.getModels();
      const model = models.length > 0 ? models[models.length - 1] : null;
      if (!model) return;
      if (currentPath.endsWith(".py")) {
        const pyMarkers = validatePython(val).map(m => ({
          startLineNumber: m.line, endLineNumber: m.line,
          startColumn: m.col, endColumn: 1000,
          message: m.message,
          severity: m.severity as unknown as MonacoNS.MarkerSeverity,
        }));
        monacoInstance.editor.setModelMarkers(model, "python-lint", pyMarkers);
      } else if (currentPath.endsWith(".sh") || currentPath.endsWith(".bash")) {
        const shMarkers = validateShell(val).map(m => ({
          startLineNumber: m.line, endLineNumber: m.line,
          startColumn: m.col, endColumn: 1000,
          message: m.message,
          severity: m.severity as unknown as MonacoNS.MarkerSeverity,
        }));
        monacoInstance.editor.setModelMarkers(model, "shell-lint", shMarkers);
      }
    });
  };

  const handleSaveAll = async () => {
    if (!skillId || !hasPendingOps) return;

    // Validate SKILL.md before saving
    const skillMd = editedContents.get("SKILL.md") ?? originalContents.get("SKILL.md") ?? "";
    const result = validateSkill(skillMd, virtualFiles, pendingDeletes);
    setValidationResult(result);
    if (!result.valid) return; // Block save on errors

    setSaving(true);
    const errors: string[] = [];

    try {
      // 1. Execute deletes (individual files)
      for (const path of pendingDeletes) {
        try { await storage.deleteFile(path); }
        catch { errors.push(`Failed to delete ${path}`); }
      }

      // 1b. Execute directory deletes (all files under dir)
      for (const dir of pendingDeleteDirs) {
        const dirFiles = skillFiles.filter(f => f.startsWith(dir + "/"));
        for (const f of dirFiles) {
          try { await storage.deleteFile(f); }
          catch { errors.push(`Failed to delete ${f}`); }
        }
      }

      // 2. Execute renames (copy + delete)
      for (const [oldPath, newPath] of pendingRenames) {
        if (!pendingDeletes.has(oldPath)) {
          try { await storage.renameFile(oldPath, newPath); }
          catch { errors.push(`Failed to rename ${oldPath} → ${newPath}`); }
        }
      }

      // 3. Save created files
      for (const [path, content] of pendingCreates) {
        const edited = editedContents.get(path) ?? content;
        try { await storage.writeFile(path, edited); }
        catch { errors.push(`Failed to create ${path}`); }
      }

      // 4. Save edited existing files
      for (const path of changedFiles) {
        if (pendingCreates.has(path) || pendingDeletes.has(path)) continue;
        const content = editedContents.get(path);
        if (content === undefined) continue;
        const actualPath = pendingRenames.get(path) ?? path;
        try { await storage.writeFile(actualPath, content); }
        catch { errors.push(`Failed to save ${actualPath}`); }
      }

      if (errors.length > 0) {
        setValidationResult({ valid: false, errors, warnings: [] });
        setSaving(false);
        return; // Don't clear state — let user retry
      }

      // Success — clear staging state
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
        storage.listFiles(),
        storage.getContent(),
      ]);
      setSkillFiles(files);
      if (content !== null) {
        originalContents.set("SKILL.md", content);
        if (currentPath === "SKILL.md") setSkillContent(content);
        const { parseFrontmatter } = await import("../../lib/skill-storage");
        const meta = parseFrontmatter(content);
        if (meta && skill) setSkill({ ...skill, name: meta.name, description: meta.description });
      }
      if (currentPath !== "SKILL.md") {
        const fc = await storage.getFile(currentPath);
        if (fc !== null) {
          setSkillContent(fc);
          originalContents.set(currentPath, fc);
        }
      }
    } finally {
      setSaving(false);
    }
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
    if (storage.isAgentMode) {
      navigate(`/agents/edit/${agentId}`);
    } else {
      navigate("/skills");
    }
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
  const [moveFileDialog, setMoveFileDialog] = useState<string | null>(null);
  const [dialogInput, setDialogInput] = useState("");

  const handleNewFile = async () => {
    if (!dialogInput.trim()) return;
    const name = dialogInput.trim();
    // Validate filename
    if (/[<>:"|?*\\]/.test(name) || name.includes("..")) {
      setValidationResult({ valid: false, errors: [`Invalid filename: ${name}`], warnings: [] });
      return;
    }
    const dir = newFileDialog?.parentDir;
    const path = dir ? `${dir}/${name}` : name;
    // Check for duplicates
    if (virtualFiles.includes(path) || pendingCreates.has(path)) {
      setValidationResult({ valid: false, errors: [`File already exists: ${path}`], warnings: [] });
      return;
    }
    pendingCreates.set(path, "");
    editedContents.set(path, "");
    setNewFileDialog(null);
    setDialogInput("");
    setChangedFiles(new Set([...changedFiles, path]));
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
    const name = dialogInput.trim();
    if (/[<>:"|?*\\]/.test(name) || name.includes("..")) {
      setValidationResult({ valid: false, errors: [`Invalid filename: ${name}`], warnings: [] });
      return;
    }
    const parts = oldPath.split("/");
    parts[parts.length - 1] = name;
    const newPath = parts.join("/");
    if (newPath !== oldPath && (virtualFiles.includes(newPath) || pendingCreates.has(newPath))) {
      setValidationResult({ valid: false, errors: [`File already exists: ${newPath}`], warnings: [] });
      return;
    }
    stageMove(oldPath, newPath);
    setRenameDialog(null);
    setDialogInput("");
  };

  const handleMoveFile = () => {
    if (!moveFileDialog || !dialogInput.trim()) return;
    const oldPath = moveFileDialog;
    const fileName = oldPath.split("/").pop()!;
    const targetDir = dialogInput.trim();
    const newPath = targetDir === "(root)" ? fileName : `${targetDir}/${fileName}`;
    stageMove(oldPath, newPath);
    setMoveFileDialog(null);
    setDialogInput("");
  };

  // Compute available directories for move dialog
  const availableDirs = useMemo(() => {
    const dirs = new Set<string>();
    dirs.add("(root)");
    for (const f of virtualFiles) {
      const idx = f.indexOf("/");
      if (idx > 0) dirs.add(f.slice(0, idx));
    }
    // Also include pending create dirs
    for (const path of pendingCreates.keys()) {
      const idx = path.indexOf("/");
      if (idx > 0) dirs.add(path.slice(0, idx));
    }
    return [...dirs].sort();
  }, [virtualFiles, pendingCreates, changedFiles]);

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

  // Ctrl+S to save
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (hasPendingOps && !saving) handleSaveAll();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [hasPendingOps, saving]);

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
        {isNew && <span className="text-[9px] text-green-500 font-medium">{t("skillEditor.newTag")}</span>}
        {isDeleted && <span className="text-[9px] text-red-400 font-medium">{t("skillEditor.delTag")}</span>}
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
      <div className={`flex items-center gap-3 px-6 py-3 border-b ${isDark ? "border-gray-700" : "border-gray-200"}`}>
        <button onClick={() => storage.isAgentMode ? navigate(`/agents/edit/${agentId}`) : navigate("/skills")} className={`p-1 rounded ${isDark ? "hover:bg-gray-800 text-gray-300" : "hover:bg-gray-100 text-gray-600"}`} title={t("common.back")}>
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className={`text-base font-semibold ${isDark ? "text-gray-100" : "text-gray-900"}`}>{skill?.name}</h2>
            {storage.isAgentMode && (
              <span className="text-[10px] px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-full">
                Agent Copy
              </span>
            )}
          </div>
          {skill?.description && (
            <p className="text-xs text-gray-400 truncate">{skill.description}</p>
          )}
        </div>
        <button onClick={async () => {
          setValidating(true);
          const allErrors: string[] = [];
          const allWarnings: string[] = [];

          // 1. Local validation: SKILL.md frontmatter + structure
          const skillMd = editedContents.get("SKILL.md") ?? originalContents.get("SKILL.md") ?? skillContent ?? "";
          const skillResult = validateSkill(skillMd, virtualFiles, pendingDeletes);
          allErrors.push(...skillResult.errors);
          allWarnings.push(...skillResult.warnings);

          // 2. Local validation: Python/Shell syntax
          const allFilesList = ["SKILL.md", ...virtualFiles];
          for (const filePath of allFilesList) {
            if (!filePath.endsWith(".py") && !filePath.endsWith(".sh") && !filePath.endsWith(".bash")) continue;
            let content = editedContents.get(filePath) ?? originalContents.get(filePath) ?? null;
            if (content === null && skillId) {
              content = await storage.getFile(filePath);
              if (content !== null) originalContents.set(filePath, content);
            }
            if (!content) continue;
            if (filePath.endsWith(".py")) {
              for (const e of validatePython(content)) allErrors.push(`${filePath}:${e.line}: ${e.message}`);
            } else if (filePath.endsWith(".sh") || filePath.endsWith(".bash")) {
              for (const e of validateShell(content)) allErrors.push(`${filePath}:${e.line}: ${e.message}`);
            }
          }

          // 3. Meta-Agent validation (deeper analysis)
          if (allErrors.length === 0) {
            try {
              const lang = useUISettings.getState().language;
              const langHint = lang === "zh" ? "用中文回复。" : "Respond in English.";
              const validatePrompt = `${langHint}
You are a reviewer for Agent Studio skills (AgentSkills.io format). Review this skill and report ONLY issues that affect functionality, correctness, or user experience.

## What to Report as Errors
- Missing or invalid YAML frontmatter fields (name, type)
- File references in frontmatter that don't exist
- Python syntax errors in referenced scripts
- Broken markdown structure that would render incorrectly

## What to Report as Warnings
- Missing description field or description too vague to be useful
- Referenced files without usage instructions in the body
- Python scripts missing docstrings or error handling for user-facing operations
- Inconsistency between frontmatter file list and actual files

## What to IGNORE (do NOT report)
- Code style preferences (import order, naming conventions)
- Minor wording improvements to descriptions
- "Could be better" suggestions without concrete impact
- Formatting preferences (heading levels, bullet styles)

SKILL.md content:
\`\`\`
${skillMd.slice(0, 6000)}
\`\`\`

Files in this skill: ${allFilesList.join(", ")}

Respond with ONLY a JSON block:
\`\`\`json
{"valid": true/false, "errors": ["..."], "warnings": ["..."]}
\`\`\``;
              let result = "";
              for await (const chunk of invokeMetaAgent(validatePrompt, [])) {
                const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
                if (cleaned) result += cleaned;
              }
              // Parse JSON from response
              const jsonMatch = result.match(/\{[\s\S]*"valid"[\s\S]*\}/);
              if (jsonMatch) {
                try {
                  const parsed = JSON.parse(jsonMatch[0]);
                  if (Array.isArray(parsed.errors)) allErrors.push(...parsed.errors);
                  if (Array.isArray(parsed.warnings)) allWarnings.push(...parsed.warnings);
                } catch { /* JSON parse failed, skip */ }
              }
            } catch { /* Meta-Agent call failed, continue with local results */ }
          }

          // 4. Update Monaco markers for current file
          const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
          if (monacoInstance && currentPath.endsWith(".py") && skillContent) {
            const model = monacoInstance.editor.getModels().find(m => m.getValue() === skillContent);
            if (model) {
              const mapped = validatePython(skillContent).map(m => ({
                startLineNumber: m.line, endLineNumber: m.line,
                startColumn: m.col, endColumn: 1000,
                message: m.message,
                severity: m.severity as unknown as MonacoNS.MarkerSeverity,
              }));
              monacoInstance.editor.setModelMarkers(model, "python-lint", mapped);
            }
          }

          if (allErrors.length === 0 && allWarnings.length === 0) {
            setValidationResult({ valid: true, errors: [], warnings: [] });
            setTimeout(() => setValidationResult(null), 2000);
          } else {
            setValidationResult({ valid: allErrors.length === 0, errors: allErrors, warnings: allWarnings });
          }
          setValidating(false);
        }}
          disabled={validating}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors disabled:opacity-50 ${isDark ? "text-gray-400 hover:text-green-400 hover:bg-green-900/30" : "text-gray-500 hover:text-green-600 hover:bg-green-50"}`}>
          {validating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
          {t("common.validate")}
        </button>
        {hasPendingOps && (
          <button onClick={() => setShowDiff(true)}
            className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-blue-400 hover:bg-blue-900/30" : "text-gray-500 hover:text-blue-600 hover:bg-blue-50"}`}>
            <GitCompare className="w-3.5 h-3.5" />
            {t("skillEditor.diff", { count: pendingCount })}
          </button>
        )}
        {hasPendingOps && (
          <button onClick={handleDiscard}
            className={`px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${isDark ? "text-gray-400 hover:text-gray-300 hover:bg-gray-800" : "text-gray-500 hover:text-gray-700 hover:bg-gray-100"}`}>
            {t("common.discard")}
          </button>
        )}
        {hasPendingOps && (
          <>
          <div className={`w-px h-5 ${isDark ? "bg-gray-700" : "bg-gray-200"} mx-0.5`} />
          <button onClick={handleSaveAll} disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {t("skillEditor.save", { count: pendingCount })}
          </button>
          </>
        )}
        {!storage.isAgentMode && (
        <button onClick={() => setShowDeleteConfirm(true)} className={`p-1.5 rounded transition-colors ${isDark ? "text-red-400 hover:bg-red-900/20" : "text-red-400 hover:text-red-600 hover:bg-red-50"}`}>
          <Trash2 className="w-4 h-4" />
        </button>
        )}
        <button onClick={() => {
          if (!skillId) return;
          const store = useSkillAssistantStore.getState();
          if (store.panelOpen) store.closePanel();
          else store.openPanel(skillId);
        }}
          className={`flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-lg transition-colors ${useSkillAssistantStore.getState().panelOpen
            ? isDark ? "bg-purple-900/30 text-purple-400" : "bg-purple-50 text-purple-600"
            : isDark ? "text-gray-400 hover:text-purple-400 hover:bg-purple-900/30" : "text-gray-500 hover:text-purple-600 hover:bg-purple-50"
          }`}
          title={t("assistant.title")}>
          <Sparkles className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Validation results */}
      {validationResult && (
        <div className={`mx-6 mt-2 rounded-lg text-sm border ${
          !validationResult.valid
            ? isDark ? "bg-red-900/20 border-red-800" : "bg-red-50 border-red-200"
            : validationResult.errors.length === 0 && validationResult.warnings.length === 0
              ? isDark ? "bg-green-900/20 border-green-800" : "bg-green-50 border-green-200"
              : isDark ? "bg-amber-900/20 border-amber-800" : "bg-amber-50 border-amber-200"
        }`}>
          <div className="px-4 py-2">
            <div className="flex items-center justify-between">
              <p className={`text-xs font-medium ${
                !validationResult.valid ? "text-red-500"
                  : validationResult.errors.length === 0 && validationResult.warnings.length === 0
                    ? isDark ? "text-green-400" : "text-green-600"
                    : "text-amber-600"
              }`}>
                {!validationResult.valid
                  ? t("validation.failed")
                  : validationResult.errors.length === 0 && validationResult.warnings.length === 0
                    ? t("validation.noIssues")
                    : t("validation.warnings")}
              </p>
            </div>
            {validationResult.errors.map((e, i) => (
              <p key={`e${i}`} className="text-[11px] text-red-500 mt-1">&#x2716; {e}</p>
            ))}
            {validationResult.warnings.map((w, i) => (
              <p key={`w${i}`} className="text-[11px] text-amber-600 mt-1">&#x26A0; {w}</p>
            ))}
            {(validationResult.errors.length > 0 || validationResult.warnings.length > 0) && (
              <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                <button onClick={async () => {
                  if (!skillId) return;
                  // Pre-load ALL file contents into memory so AI can fix across files
                  const allFilesList = ["SKILL.md", ...virtualFiles];
                  for (const filePath of allFilesList) {
                    if (editedContents.has(filePath) || originalContents.has(filePath)) continue;
                    const content = filePath === "SKILL.md"
                      ? await storage.getContent()
                      : await storage.getFile(filePath);
                    if (content !== null) originalContents.set(filePath, content);
                  }
                  const store = useSkillAssistantStore.getState();
                  if (!store.panelOpen) store.openPanel(skillId);
                  const issues = [...validationResult.errors, ...validationResult.warnings].join("\n");
                  const autoFixPrompt = `## Auto-Fix Task\nFix ONLY the following validation issues. Do NOT remove or rewrite any existing content.\n\nIssues:\n${issues}\n\nRules:\n- Use __file_edit (search/replace) ONLY. Do NOT use __file_update.\n- Fix ONLY the specific issues listed above.\n- NEVER delete existing content, sections, or descriptions.\n- NEVER shorten or summarize existing text.\n- Make minimal, surgical changes.\n- If an issue appears already fixed in the current file content, skip it and say so.\n- If SEARCH text cannot be found, the issue may have been fixed already — do NOT attempt alternative fixes.`;
                  store.sendMessage(
                    autoFixPrompt,
                    { path: currentPath, content: skillContent ?? "", allFiles: allFilesList, getFileContent: (p: string) => editedContents.get(p) ?? originalContents.get(p) ?? null },
                    (path: string, newContent: string) => {
                      editedContents.set(path, newContent);
                      const orig = originalContents.get(path);
                      const next = new Set(changedFiles);
                      if (orig !== undefined && newContent !== orig) next.add(path);
                      else if (orig === undefined) { pendingCreates.set(path, ""); next.add(path); }
                      setChangedFiles(next);
                      if (path === currentPath) setSkillContent(newContent);
                    },
                  );
                  setValidationResult(null);
                }}
                  className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium bg-green-600 text-white rounded hover:bg-green-700 transition-colors">
                  <Sparkles className="w-3 h-3" />
                  {t("common.autoFix")}
                </button>
                <button onClick={() => setValidationResult(null)} className="text-[10px] text-gray-400 hover:text-gray-600">{t("common.dismiss")}</button>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar: react-arborist file tree */}
        {!sidebarCollapsed && (
          <div style={{ width: sidebarWidth }} className={`border-r overflow-hidden flex-shrink-0 flex flex-col ${isDark ? "border-gray-700 bg-[#252526]" : "border-gray-200 bg-gray-50"}`}>
            <div className={`px-3 py-2 flex items-center justify-between ${isDark ? "text-gray-500" : "text-gray-400"}`}>
              <span className="text-[10px] font-semibold uppercase tracking-wider">Files</span>
              <div className="flex items-center gap-0.5">
                <button onClick={() => { setNewFileDialog({ parentDir: "" }); setDialogInput(""); }}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title={t("skillEditor.newFile")}>
                  <Plus className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => { setNewFolderDialog(true); setNewFolderParent(""); setDialogInput(""); }}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title={t("skillEditor.newFolder")}>
                  <FolderPlus className="w-3.5 h-3.5" />
                </button>
                <button onClick={() => setSidebarCollapsed(true)}
                  className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`}
                  title={t("skillEditor.hideSidebar")}>
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
              disableEdit
              disableDrag={(node) => node.id === "SKILL.md" || pendingDeletes.has(node.id)}
              disableDrop={(args) => {
                // Can't drop onto files, only folders or root
                if (args.parentNode && !args.parentNode.isInternal) return true;
                return false;
              }}
              onMove={({ dragIds, parentId }) => {
                for (const id of dragIds) {
                  if (id === "SKILL.md" || id.startsWith("__dir__")) continue;
                  const fileName = id.split("/").pop()!;
                  const newDir = parentId?.replace("__dir__", "") ?? "";
                  const newPath = newDir ? `${newDir}/${fileName}` : fileName;
                  stageMove(id, newPath);
                }
              }}
            >
              {FileNode}
            </Tree>
          </div>
        </div>
        )}
        {sidebarCollapsed && (
          <button onClick={() => setSidebarCollapsed(false)}
            className={`flex-shrink-0 px-1 py-4 border-r ${isDark ? "border-gray-700 bg-[#252526] text-gray-500 hover:text-gray-300" : "border-gray-200 bg-gray-50 text-gray-400 hover:text-gray-600"}`}
            title={t("skillEditor.showSidebar")}>
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
                {(currentPath.endsWith(".py") || currentPath.endsWith(".sh")) && (
                  <button
                    onClick={async () => {
                      if (running || !skillContent) return;
                      setRunning(true);
                      setRunOutput("Running...");
                      try {
                        const lang = currentPath.endsWith(".py") ? "python" : "shell";
                        const prompt = `Run this ${lang} code and return ONLY the output. No explanation.\n\nUse run_command tool with language="${lang === "shell" ? "shell" : "python"}".\n\nCode:\n\`\`\`\n${skillContent}\n\`\`\``;
                        let result = "";
                        for await (const chunk of invokeMetaAgent(prompt, [])) {
                          const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
                          if (cleaned) result += cleaned;
                        }
                        setRunOutput(result.trim() || "(no output)");
                      } catch (err) {
                        setRunOutput(`Error: ${err instanceof Error ? err.message : "Unknown error"}`);
                      } finally {
                        setRunning(false);
                      }
                    }}
                    disabled={running}
                    className={`ml-2 flex items-center gap-1 px-2 py-0.5 text-[11px] rounded ${
                      isDark ? "text-green-400 hover:bg-green-900/30" : "text-green-600 hover:bg-green-50"
                    } transition-colors disabled:opacity-50`}
                    title={t("skillEditor.runScript")}
                  >
                    {running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                    {t("common.run")}
                  </button>
                )}
              </div>
              <div className="flex-1">
                <Editor
                  value={skillContent}
                  onChange={handleEditorChange}
                  language={getMonacoLanguage(currentPath)}
                  theme={isDark ? "vs-dark" : "light"}
                  onMount={(editor, monaco) => {
                    // Auto-run Python/Shell validation on file load
                    const model = editor.getModel();
                    if (model && skillContent) {
                      if (currentPath.endsWith(".py")) {
                        const pyMarkers = validatePython(skillContent).map(m => ({
                          startLineNumber: m.line, endLineNumber: m.line,
                          startColumn: m.col, endColumn: 1000,
                          message: m.message,
                          severity: m.severity as unknown as MonacoNS.MarkerSeverity,
                        }));
                        monaco.editor.setModelMarkers(model, "python-lint", pyMarkers);
                      } else if (currentPath.endsWith(".sh") || currentPath.endsWith(".bash")) {
                        const shMarkers = validateShell(skillContent).map(m => ({
                          startLineNumber: m.line, endLineNumber: m.line,
                          startColumn: m.col, endColumn: 1000,
                          message: m.message,
                          severity: m.severity as unknown as MonacoNS.MarkerSeverity,
                        }));
                        monaco.editor.setModelMarkers(model, "shell-lint", shMarkers);
                      }
                    }
                  }}
                  onValidate={(markers) => {
                    // Monaco fires onValidate for JSON/JS/TS automatically
                    // For Python/Shell, add our custom markers
                    const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
                    if (!monacoInstance || !skillContent) { void markers; return; }
                    const model = monacoInstance.editor.getModels().find(m => m.getValue() === skillContent);
                    if (!model) { void markers; return; }

                    if (currentPath.endsWith(".py")) {
                      const pyMarkers = validatePython(skillContent).map(m => ({
                        startLineNumber: m.line, endLineNumber: m.line,
                        startColumn: m.col, endColumn: 1000,
                        message: m.message,
                        severity: m.severity as unknown as MonacoNS.MarkerSeverity,
                      }));
                      monacoInstance.editor.setModelMarkers(model, "python-lint", pyMarkers);
                    } else if (currentPath.endsWith(".sh") || currentPath.endsWith(".bash")) {
                      const shMarkers = validateShell(skillContent).map(m => ({
                        startLineNumber: m.line, endLineNumber: m.line,
                        startColumn: m.col, endColumn: 1000,
                        message: m.message,
                        severity: m.severity as unknown as MonacoNS.MarkerSeverity,
                      }));
                      monacoInstance.editor.setModelMarkers(model, "shell-lint", shMarkers);
                    }
                    void markers;
                  }}
                  beforeMount={(monaco) => {
                    // Enable JSON validation
                    monaco.languages.json?.jsonDefaults?.setDiagnosticsOptions?.({
                      validate: true,
                      allowComments: false,
                      schemaValidation: "error",
                    });
                    // Enable JS/TS validation
                    monaco.languages.typescript?.javascriptDefaults?.setDiagnosticsOptions?.({
                      noSemanticValidation: false,
                      noSyntaxValidation: false,
                    });
                    monaco.languages.typescript?.typescriptDefaults?.setDiagnosticsOptions?.({
                      noSemanticValidation: false,
                      noSyntaxValidation: false,
                    });
                  }}
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
              {/* Run output panel */}
              {runOutput !== null && (
                <div className={`border-t flex-shrink-0 ${isDark ? "border-gray-700 bg-gray-900" : "border-gray-200 bg-gray-50"} max-h-48 overflow-auto`}>
                  <div className={`flex items-center justify-between px-3 py-1 ${isDark ? "bg-gray-800" : "bg-gray-100"}`}>
                    <span className={`text-[10px] font-semibold uppercase tracking-wider ${isDark ? "text-gray-500" : "text-gray-400"}`}>{t("skillEditor.output")}</span>
                    <button onClick={() => setRunOutput(null)} className={`text-[10px] ${isDark ? "text-gray-500 hover:text-gray-300" : "text-gray-400 hover:text-gray-600"}`}>{t("common.close")}</button>
                  </div>
                  <pre className={`px-3 py-2 text-[11px] font-mono whitespace-pre-wrap ${isDark ? "text-gray-300" : "text-gray-700"}`}>
                    {runOutput}
                  </pre>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-400 p-6">Failed to load content.</p>
          )}
        </div>

        {/* AI Assistant panel */}
        {skillId && (
          <SkillAssistant
            skillId={skillId}
            currentPath={currentPath}
            currentContent={skillContent ?? ""}
            allFiles={["SKILL.md", ...virtualFiles]}
            getFileContent={(path) => editedContents.get(path) ?? originalContents.get(path) ?? null}
            onFileUpdate={(path, newContent) => {
              editedContents.set(path, newContent);
              const original = originalContents.get(path);
              const next = new Set(changedFiles);
              if (original !== undefined && newContent !== original) {
                next.add(path);
              } else if (original === undefined) {
                // New file created by assistant
                if (!pendingCreates.has(path)) pendingCreates.set(path, "");
                next.add(path);
              }
              setChangedFiles(next);
              // If updating the currently open file, refresh editor
              if (path === currentPath) {
                setSkillContent(newContent);
              }
            }}
          />
        )}
      </div>

      {showDiff && <DiffModal changes={getDiffChanges()} onClose={() => setShowDiff(false)} isDark={isDark} />}

      {/* Unsaved changes blocker */}
      {blocker.state === "blocked" && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center">
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.unsavedChanges")}</p>
            <p className="text-xs text-gray-500 mb-4">
              {t("skillEditor.unsavedDesc", { count: changedFiles.size })}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => blocker.reset?.()} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("skillEditor.stay")}</button>
              <button onClick={() => blocker.proceed?.()} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("skillEditor.discardLeave")}</button>
            </div>
          </div>
        </div>
      )}

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setShowDeleteConfirm(false)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.moveToTrash")}</p>
            <p className="text-xs text-gray-500 mb-4">
              <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{skill?.name}</span> will be moved to trash. You can restore it later from the Skills page.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowDeleteConfirm(false)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleDelete} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("skillEditor.moveToTrashBtn")}</button>
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
          {!contextMenu.isFolder && (
            <button onClick={() => { setMoveFileDialog(contextMenu.nodeId); setDialogInput(""); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <ArrowRightLeft className="w-3 h-3" /> Move to...
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
              placeholder={t("skillEditor.filenamePlaceholder")}
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
              placeholder={t("skillEditor.folderPlaceholder")}
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

      {/* Move file dialog */}
      {moveFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setMoveFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>Move file</p>
            <p className="text-xs text-gray-500 mb-3">
              Move <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{moveFileDialog.split("/").pop()}</span> to:
            </p>
            <div className="space-y-1 max-h-40 overflow-y-auto mb-3">
              {availableDirs.map(dir => {
                const currentDir = moveFileDialog.includes("/") ? moveFileDialog.slice(0, moveFileDialog.indexOf("/")) : "(root)";
                const isCurrent = dir === currentDir;
                return (
                  <button key={dir} onClick={() => setDialogInput(dir)}
                    className={`w-full text-left px-2.5 py-1.5 text-xs rounded-lg flex items-center gap-2 ${
                      dialogInput === dir
                        ? "bg-blue-600 text-white"
                        : isCurrent
                          ? isDark ? "bg-gray-700 text-gray-400" : "bg-gray-100 text-gray-400"
                          : isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"
                    }`}>
                    {dir === "(root)" ? <File className="w-3 h-3" /> : <FolderClosed className="w-3 h-3 text-yellow-500" />}
                    {dir === "(root)" ? "Root" : dir}
                    {isCurrent && <span className="text-[9px] ml-auto opacity-60">current</span>}
                  </button>
                );
              })}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setMoveFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>Cancel</button>
              <button onClick={handleMoveFile} disabled={!dialogInput} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">Move</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
