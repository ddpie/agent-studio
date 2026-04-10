import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft, Trash2, Loader2, Save, GitCompare,
  FolderClosed, File, ChevronRight as ChevronRightIcon,
  Plus, Pencil, FolderPlus, ArrowRightLeft, Sparkles, ShieldCheck, Play,
} from "lucide-react";
import { deleteSkill, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";
import { useSkillStorage } from "../../hooks/useSkillStorage";
import { useFileEditor } from "../../hooks/useFileEditor";
import Editor from "@monaco-editor/react";
import type * as MonacoNS from "monaco-editor";
import { Tree, type NodeRendererProps } from "react-arborist";
import { useUISettings } from "../../stores/ui-settings-store";
import { useSkillAssistantStore } from "../../stores/skill-assistant-store";
import SkillAssistant from "../skills/SkillAssistant";
import ValidationBanner from "../shared/ValidationBanner";
import DiffModal from "../shared/DiffModal";
import ConfirmDialog from "../ui/ConfirmDialog";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { preloadPyodide } from "../../lib/pyodide-checker";
import { getMonacoLanguage } from "../../lib/monaco-helpers";
import type { ValidationResult } from "../../lib/types/validation";
import useIsDark from "../../hooks/useIsDark";
import useUnsavedGuard from "../../hooks/useUnsavedGuard";
import { validatePython } from "../../lib/validators/python-validator";
import { validateShell } from "../../lib/validators/shell-validator";
import { validateSkill } from "../../lib/validators/skill-validator";
import { getFileIcon, type TreeNode } from "../../lib/tree-helpers";

// --- Main Component ---

export default function SkillDetail() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
  const isDark = useIsDark();
  const { t } = useTranslation();
  const [skill, setSkill] = useState<SkillIndexEntry | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const agentId = searchParams.get("agentId");
  const agentSkillId = searchParams.get("agentSkillId");
  const storage = useSkillStorage(skillId!, agentId, agentSkillId);
  const activeFile = searchParams.get("file");
  const [showDiff, setShowDiff] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(224);
  const dragging = useRef(false);
  const [runOutput, setRunOutput] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const editor = useFileEditor({
    storage,
    onFileSwitch: (path) => setSearchParams(path === "SKILL.md" ? {} : { file: path }),
  });

  const currentPath = activeFile || "SKILL.md";
  const skillContent = editor.content;

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

  const { virtualFiles, treeData, hasPendingOps, pendingCount, changedFiles, pendingDeletes, pendingDeleteDirs } = editor;

  useEffect(() => {
    if (!skillId) return;
    const fileParam = searchParams.get("file");
    Promise.all([
      listSkills(),
      editor.loadInitial(fileParam || undefined),
    ]).then(([skills]) => {
      const found = skills.find(s => s.id === skillId) || null;
      setSkill(found);
    });
    // Auto-open AI assistant
    useSkillAssistantStore.getState().openPanel(skillId);
    // Warm up Pyodide for Python syntax checking
    preloadPyodide();
  }, [skillId, storage]);

  const handleNodeClick = async (nodeId: string) => {
    if (nodeId.startsWith("__dir__")) return;
    if (nodeId === currentPath) return;
    setValidationResult(null);
    if (nodeId === "SKILL.md") {
      setSearchParams({});
    } else {
      setSearchParams({ file: nodeId });
    }
    await editor.selectFile(nodeId);
  };

  const handleEditorChange = (val: string | undefined) => {
    if (val === undefined) return;
    editor.handleEditorChange(val);

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
    const result = await editor.handleSaveAll();
    if (!result.success) {
      setValidationResult(result.validationResult ?? { valid: false, errors: result.errors, warnings: [] });
      return;
    }
    // Update skill metadata from saved SKILL.md
    const newContent = editor.content;
    if (newContent !== null && skill) {
      const { parseFrontmatter } = await import("../../lib/skill-storage");
      const meta = parseFrontmatter(newContent);
      if (meta) setSkill({ ...skill, name: meta.name, description: meta.description });
    }
  };

  const handleDiscard = () => {
    editor.handleDiscard();
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

  const getDiffChanges = () => editor.getDiffChanges();

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
    if (/[<>:"|?*\\]/.test(name) || name.includes("..")) {
      setValidationResult({ valid: false, errors: [`Invalid filename: ${name}`], warnings: [] });
      return;
    }
    const dir = newFileDialog?.parentDir;
    const path = dir ? `${dir}/${name}` : name;
    if (!editor.stageNewFile(path)) {
      setValidationResult({ valid: false, errors: [`File already exists: ${path}`], warnings: [] });
      return;
    }
    setNewFileDialog(null);
    setDialogInput("");
    setSearchParams({ file: path });
    editor.setContent("");
    editor.setCurrentFile(path);
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

    if (isDir) {
      const dirName = target.replace("__dir__", "");
      editor.stageDeleteDir(dirName);
      if (currentPath.startsWith(dirName + "/")) {
        setSearchParams({});
      }
    } else {
      editor.stageDelete(target);
      if (target === currentPath) {
        setSearchParams({});
      }
    }
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
    if (newPath !== oldPath && (virtualFiles.includes(newPath) || editor.pendingCreates.has(newPath))) {
      setValidationResult({ valid: false, errors: [`File already exists: ${newPath}`], warnings: [] });
      return;
    }
    editor.stageMove(oldPath, newPath);
    setRenameDialog(null);
    setDialogInput("");
  };

  const handleMoveFile = () => {
    if (!moveFileDialog || !dialogInput.trim()) return;
    const oldPath = moveFileDialog;
    const fileName = oldPath.split("/").pop()!;
    const targetDir = dialogInput.trim();
    const newPath = targetDir === "(root)" ? fileName : `${targetDir}/${fileName}`;
    editor.stageMove(oldPath, newPath);
    setMoveFileDialog(null);
    setDialogInput("");
  };

  const availableDirs = useMemo(() => {
    const dirs = new Set<string>();
    dirs.add("(root)");
    for (const f of virtualFiles) {
      const idx = f.indexOf("/");
      if (idx > 0) dirs.add(f.slice(0, idx));
    }
    for (const path of editor.pendingCreates.keys()) {
      const idx = path.indexOf("/");
      if (idx > 0) dirs.add(path.slice(0, idx));
    }
    return [...dirs].sort();
  }, [virtualFiles, editor.pendingCreates, changedFiles]);

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

  // Unified unsaved changes guard (beforeunload + Ctrl+S + route blocker)
  const blocker = useUnsavedGuard({
    hasChanges: hasPendingOps,
    onSave: handleSaveAll,
    saving: editor.saving,
  });

  // Sidebar collapse
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [treeHeight, setTreeHeight] = useState(400);
  const treeContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = treeContainerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setTreeHeight(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [sidebarCollapsed]);

  // Custom tree node renderer
  const FileNode = useCallback(({ node, style }: NodeRendererProps<TreeNode>) => {
    const isFolder = node.isInternal;
    const isActive = !isFolder && node.id === currentPath;
    const isChanged = changedFiles.has(node.id);
    const isDeleted = pendingDeletes.has(node.id) || (isFolder && pendingDeleteDirs.has(node.id.replace("__dir__", "")));
    const isNew = editor.pendingCreates.has(node.id);
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
  }, [currentPath, changedFiles, isDark, pendingDeletes, editor.pendingCreates]);

  if (!skill && !editor.loadingContent) {
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
          const skillMd = editor.getEditedContent("SKILL.md") ?? editor.getOriginalContent("SKILL.md") ?? skillContent ?? "";
          const skillResult = validateSkill(skillMd, virtualFiles, pendingDeletes);
          allErrors.push(...skillResult.errors);
          allWarnings.push(...skillResult.warnings);

          // 2. Local validation: Python/Shell syntax
          const allFilesList = ["SKILL.md", ...virtualFiles];
          for (const filePath of allFilesList) {
            if (!filePath.endsWith(".py") && !filePath.endsWith(".sh") && !filePath.endsWith(".bash")) continue;
            let content = editor.getEditedContent(filePath) ?? editor.getOriginalContent(filePath) ?? null;
            if (content === null && skillId) {
              content = await storage.getFile(filePath);
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
          <button onClick={handleSaveAll} disabled={editor.saving}
            className="flex items-center gap-1.5 px-3.5 py-1.5 text-[12px] font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 shadow-sm transition-all">
            {editor.saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
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
      <ValidationBanner
        result={validationResult}
        onDismiss={() => setValidationResult(null)}
        onAutoFix={async () => {
          if (!skillId) return;
          const allFilesList = ["SKILL.md", ...virtualFiles];
          for (const filePath of allFilesList) {
            if (editor.getEditedContent(filePath) !== undefined || editor.getOriginalContent(filePath) !== undefined) continue;
            const content = filePath === "SKILL.md"
              ? await storage.getContent()
              : await storage.getFile(filePath);
            if (content !== null) editor.setEditedContent(filePath, content);
          }
          const store = useSkillAssistantStore.getState();
          if (!store.panelOpen) store.openPanel(skillId);
          const issues = [...(validationResult?.errors ?? []), ...(validationResult?.warnings ?? [])].join("\n");
          const autoFixPrompt = `## Auto-Fix Task\nFix ONLY the following validation issues. Do NOT remove or rewrite any existing content.\n\nIssues:\n${issues}\n\nRules:\n- Use __file_content (4 backticks) to output the COMPLETE fixed file.\n- Fix ONLY the specific issues listed above.\n- NEVER delete existing content, sections, or descriptions.\n- NEVER shorten or summarize existing text.\n- If an issue appears already fixed in the current file content, skip it and say so.\n- Do NOT ask for confirmation. Execute fixes immediately.`;
          store.sendMessage(
            autoFixPrompt,
            { path: currentPath, content: skillContent ?? "", allFiles: allFilesList, getFileContent: (p: string) => editor.getEditedContent(p) ?? editor.getOriginalContent(p) ?? null },
            (path: string, newContent: string) => {
              editor.markNewFromExternal(path, newContent);
            },
          );
          setValidationResult(null);
        }}
      />

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
          <div className="flex-1 overflow-hidden" ref={treeContainerRef}>
            <Tree
              data={treeData}
              openByDefault
              width={sidebarWidth}
              height={treeHeight}
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
                  editor.stageMove(id, newPath);
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
          {editor.loadingContent ? (
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
                    contextmenu: false,
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
            getFileContent={(path) => editor.getEditedContent(path) ?? editor.getOriginalContent(path) ?? null}
            onFileUpdate={(path, newContent) => {
              editor.markNewFromExternal(path, newContent);
            }}
          />
        )}
      </div>

      {showDiff && <DiffModal changes={getDiffChanges()} onClose={() => setShowDiff(false)} />}

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title={t("skillEditor.unsavedChanges")}
        message={t("skillEditor.unsavedDesc", { count: changedFiles.size })}
        confirmLabel={t("skillEditor.discardLeave")}
        cancelLabel={t("skillEditor.stay")}
        danger
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />

      <ConfirmDialog
        open={showDeleteConfirm}
        title={t("skillEditor.moveToTrash")}
        message={t("skillEditor.moveToTrashDesc", { name: skill?.name })}
        confirmLabel={t("skillEditor.moveToTrashBtn")}
        cancelLabel={t("common.cancel")}
        danger
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />

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
                <Plus className="w-3 h-3" /> {t("skillEditor.newFileRoot")}
              </button>
              <button onClick={() => {
                const parent = contextMenu.nodeId.replace("__dir__", "");
                setNewFolderParent(parent);
                setNewFolderDialog(true);
                setDialogInput("");
                setContextMenu(null);
              }}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
                <FolderPlus className="w-3 h-3" /> {t("skillEditor.newFolderTitle")}
              </button>
            </>
          )}
          {!contextMenu.isFolder && (
            <button onClick={() => { setRenameDialog({ path: contextMenu.nodeId, currentName: contextMenu.nodeId.split("/").pop()! }); setDialogInput(contextMenu.nodeId.split("/").pop()!); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <Pencil className="w-3 h-3" /> {t("skillEditor.renameFile")}
            </button>
          )}
          {!contextMenu.isFolder && (
            <button onClick={() => { setMoveFileDialog(contextMenu.nodeId); setDialogInput(""); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <ArrowRightLeft className="w-3 h-3" /> {t("skillEditor.moveTo")}
            </button>
          )}
          <button onClick={() => { setDeleteFileDialog(contextMenu.nodeId); setContextMenu(null); }}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 text-red-400 ${isDark ? "hover:bg-gray-700" : "hover:bg-red-50"}`}>
            <Trash2 className="w-3 h-3" /> {t("common.delete")}
          </button>
        </div>
      )}

      {/* New file dialog */}
      {newFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setNewFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>
              {newFileDialog.parentDir ? t("skillEditor.newFileIn", { dir: newFileDialog.parentDir }) : t("skillEditor.newFileRoot")}
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
              <button onClick={() => setNewFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleNewFile} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("common.create")}</button>
            </div>
          </div>
        </div>
      )}

      {/* New folder dialog */}
      {newFolderDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setNewFolderDialog(false)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.newFolderTitle")}</p>
            <input
              autoFocus
              value={dialogInput}
              onChange={e => setDialogInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleNewFolder(); if (e.key === "Escape") setNewFolderDialog(false); }}
              placeholder={t("skillEditor.folderPlaceholder")}
              className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setNewFolderDialog(false)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleNewFolder} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("common.create")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Rename dialog */}
      {renameDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setRenameDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-3 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.renameFile")}</p>
            <input
              autoFocus
              value={dialogInput}
              onChange={e => setDialogInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") handleRename(); if (e.key === "Escape") setRenameDialog(null); }}
              className={`w-full px-2.5 py-1.5 text-xs border rounded-lg outline-none ${isDark ? "bg-gray-900 border-gray-700 text-gray-200" : "bg-white border-gray-200 text-gray-800"} focus:ring-1 focus:ring-blue-500`}
            />
            <div className="flex justify-end gap-2 mt-3">
              <button onClick={() => setRenameDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleRename} disabled={!dialogInput.trim()} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("common.rename")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete file confirm */}
      {deleteFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setDeleteFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.deleteFile")}</p>
            <p className="text-xs text-gray-500 mb-4">
              {t("skillEditor.deleteFileDesc", { name: deleteFileDialog })}
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeleteFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleDeleteFile} className="px-3 py-1.5 text-xs font-medium bg-red-500 text-white rounded-lg hover:bg-red-600">{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}

      {/* Move file dialog */}
      {moveFileDialog && (
        <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center" onClick={() => setMoveFileDialog(null)}>
          <div className={`${isDark ? "bg-gray-800" : "bg-white"} rounded-xl shadow-2xl p-5 max-w-sm mx-4 w-80`} onClick={e => e.stopPropagation()}>
            <p className={`text-sm font-medium mb-1 ${isDark ? "text-gray-200" : "text-gray-800"}`}>{t("skillEditor.moveFile")}</p>
            <p className="text-xs text-gray-500 mb-3">
              {t("skillEditor.moveTo")} <span className={`font-mono font-medium ${isDark ? "text-gray-300" : "text-gray-700"}`}>{moveFileDialog.split("/").pop()}</span>
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
                    {dir === "(root)" ? t("skillEditor.moveToRoot") : dir}
                    {isCurrent && <span className="text-[9px] ml-auto opacity-60">{t("skillEditor.moveCurrent")}</span>}
                  </button>
                );
              })}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setMoveFileDialog(null)} className={`px-3 py-1.5 text-xs ${isDark ? "text-gray-400 hover:bg-gray-700" : "text-gray-500 hover:bg-gray-100"} rounded-lg`}>{t("common.cancel")}</button>
              <button onClick={handleMoveFile} disabled={!dialogInput} className="px-3 py-1.5 text-xs font-medium bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50">{t("skillEditor.moveFile")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
