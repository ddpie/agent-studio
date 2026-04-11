import { useEffect, useState, useMemo, useCallback } from "react";
import { useParams, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { deleteSkill, listSkills, type SkillIndexEntry } from "../../lib/skill-storage";
import { useSkillStorage } from "../../hooks/useSkillStorage";
import { useFileEditor } from "../../hooks/useFileEditor";
import type * as MonacoNS from "monaco-editor";
import { useUISettings } from "../../stores/ui-settings-store";
import { useSkillAssistantStore } from "../../stores/skill-assistant-store";
import SkillAssistant from "../skills/SkillAssistant";
import ValidationBanner from "../shared/ValidationBanner";
import DiffModal from "../shared/DiffModal";
import ConfirmDialog from "../ui/ConfirmDialog";
import { invokeMetaAgent } from "../../lib/agentcore-client";
import { preloadPyodide } from "../../lib/pyodide-checker";
import type { ValidationResult } from "../../lib/types/validation";
import useUnsavedGuard from "../../hooks/useUnsavedGuard";
import useResizable from "../../hooks/useResizable";
import { applyLintMarkers } from "../../lib/monaco-lint";
import { validatePython } from "../../lib/validators/python-validator";
import { validateShell } from "../../lib/validators/shell-validator";
import { validateSkill } from "../../lib/validators/skill-validator";
import SkillToolbar from "./SkillToolbar";
import SkillFileTree from "./SkillFileTree";
import SkillEditorPane from "./SkillEditorPane";
import SkillFileDialogs, { type DialogState, type DialogType } from "./SkillFileDialogs";

export default function SkillDetail() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
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
  const [activeDialog, setActiveDialog] = useState<DialogState | null>(null);
  const { size: sidebarWidth, dragHandleProps: sidebarDragProps } = useResizable(224, { min: 120, max: 500, direction: "horizontal" });

  const editor = useFileEditor({
    storage,
    onFileSwitch: (path) => setSearchParams(path === "SKILL.md" ? {} : { file: path }),
  });

  const currentPath = activeFile || "SKILL.md";
  const skillContent = editor.content;
  const { virtualFiles, treeData, hasPendingOps, pendingCount, changedFiles, pendingDeletes, pendingDeleteDirs } = editor;

  useEffect(() => {
    if (!skillId) return;
    const fileParam = searchParams.get("file");
    Promise.all([listSkills(), editor.loadInitial(fileParam || undefined)]).then(([skills]) => {
      setSkill(skills.find(s => s.id === skillId) || null);
    });
    useSkillAssistantStore.getState().openPanel(skillId);
    preloadPyodide();
  }, [skillId, storage]);

  const handleNodeClick = async (nodeId: string) => {
    if (nodeId.startsWith("__dir__") || nodeId === currentPath) return;
    setValidationResult(null);
    setSearchParams(nodeId === "SKILL.md" ? {} : { file: nodeId });
    await editor.selectFile(nodeId);
  };

  const handleEditorChange = (val: string | undefined) => {
    if (val === undefined) return;
    editor.handleEditorChange(val);
    requestAnimationFrame(() => {
      const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
      if (!monacoInstance) return;
      const models = monacoInstance.editor.getModels();
      const model = models.length > 0 ? models[models.length - 1] : null;
      if (model) applyLintMarkers(monacoInstance, model, currentPath, val);
    });
  };

  const handleSaveAll = async () => {
    if (!skillId || !hasPendingOps) return;
    const result = await editor.handleSaveAll();
    if (!result.success) {
      setValidationResult(result.validationResult ?? { valid: false, errors: result.errors, warnings: [] });
      return;
    }
    const newContent = editor.content;
    if (newContent !== null && skill) {
      const { parseFrontmatter } = await import("../../lib/skill-storage");
      const meta = parseFrontmatter(newContent);
      if (meta) setSkill({ ...skill, name: meta.name, description: meta.description });
    }
  };

  const handleDelete = async () => {
    if (!skillId) return;
    await deleteSkill(skillId);
    navigate(storage.isAgentMode ? `/agents/edit/${agentId}` : "/skills");
  };

  const handleValidate = async () => {
    setValidating(true);
    const allErrors: string[] = [];
    const allWarnings: string[] = [];
    const skillMd = editor.getEditedContent("SKILL.md") ?? editor.getOriginalContent("SKILL.md") ?? skillContent ?? "";
    const skillResult = validateSkill(skillMd, virtualFiles, pendingDeletes);
    allErrors.push(...skillResult.errors);
    allWarnings.push(...skillResult.warnings);

    const allFilesList = ["SKILL.md", ...virtualFiles];
    for (const filePath of allFilesList) {
      if (!filePath.endsWith(".py") && !filePath.endsWith(".sh") && !filePath.endsWith(".bash")) continue;
      let content = editor.getEditedContent(filePath) ?? editor.getOriginalContent(filePath) ?? null;
      if (content === null && skillId) content = await storage.getFile(filePath);
      if (!content) continue;
      if (filePath.endsWith(".py")) for (const e of validatePython(content)) allErrors.push(`${filePath}:${e.line}: ${e.message}`);
      else for (const e of validateShell(content)) allErrors.push(`${filePath}:${e.line}: ${e.message}`);
    }

    if (allErrors.length === 0) {
      try {
        const lang = useUISettings.getState().language;
        const langHint = lang === "zh" ? "用中文回复。" : "Respond in English.";
        const validatePrompt = `${langHint}\nYou are a reviewer for Agent Studio skills (AgentSkills.io format). Review this skill and report ONLY issues that affect functionality, correctness, or user experience.\n\n## What to Report as Errors\n- Missing or invalid YAML frontmatter fields (name, type)\n- File references in frontmatter that don't exist\n- Python syntax errors in referenced scripts\n- Broken markdown structure that would render incorrectly\n\n## What to Report as Warnings\n- Missing description field or description too vague to be useful\n- Referenced files without usage instructions in the body\n- Python scripts missing docstrings or error handling for user-facing operations\n- Inconsistency between frontmatter file list and actual files\n\n## What to IGNORE (do NOT report)\n- Code style preferences (import order, naming conventions)\n- Minor wording improvements to descriptions\n- "Could be better" suggestions without concrete impact\n- Formatting preferences (heading levels, bullet styles)\n\nSKILL.md content:\n\`\`\`\n${skillMd.slice(0, 6000)}\n\`\`\`\n\nFiles in this skill: ${allFilesList.join(", ")}\n\nRespond with ONLY a JSON block:\n\`\`\`json\n{"valid": true/false, "errors": ["..."], "warnings": ["..."]}\n\`\`\``;
        let result = "";
        for await (const chunk of invokeMetaAgent(validatePrompt, [])) {
          const cleaned = chunk.replace(/\{"__tool"[^}]*\}/g, "");
          if (cleaned) result += cleaned;
        }
        const jsonMatch = result.match(/\{[\s\S]*"valid"[\s\S]*\}/);
        if (jsonMatch) {
          try {
            const parsed = JSON.parse(jsonMatch[0]);
            if (Array.isArray(parsed.errors)) allErrors.push(...parsed.errors);
            if (Array.isArray(parsed.warnings)) allWarnings.push(...parsed.warnings);
          } catch { /* skip */ }
        }
      } catch { /* skip */ }
    }

    if (allErrors.length === 0 && allWarnings.length === 0) {
      setValidationResult({ valid: true, errors: [], warnings: [] });
      setTimeout(() => setValidationResult(null), 2000);
    } else {
      setValidationResult({ valid: allErrors.length === 0, errors: allErrors, warnings: allWarnings });
    }
    // Update Monaco markers for current file
    const monacoInstance = (window as unknown as { monaco?: typeof MonacoNS }).monaco;
    if (monacoInstance && skillContent) {
      const model = monacoInstance.editor.getModels().find(m => m.getValue() === skillContent);
      if (model) applyLintMarkers(monacoInstance, model, currentPath, skillContent);
    }
    setValidating(false);
  };

  // File dialog handlers
  const handleDialogConfirm = useCallback((type: DialogType, value: string) => {
    if (!value.trim()) return;
    const val = value.trim();
    if (type === "newFile") {
      if (/[<>:"|?*\\]/.test(val) || val.includes("..")) {
        setValidationResult({ valid: false, errors: [`Invalid filename: ${val}`], warnings: [] });
        return;
      }
      const dir = activeDialog?.context.parentDir;
      const path = dir ? `${dir}/${val}` : val;
      if (!editor.stageNewFile(path)) {
        setValidationResult({ valid: false, errors: [`File already exists: ${path}`], warnings: [] });
        return;
      }
      setSearchParams({ file: path });
      editor.setContent("");
      editor.setCurrentFile(path);
    } else if (type === "newFolder") {
      const parent = activeDialog?.context.parentDir;
      const folderName = parent ? `${parent}/${val}` : val;
      setActiveDialog({ type: "newFile", context: { parentDir: folderName } });
      return;
    } else if (type === "rename") {
      const oldPath = activeDialog?.context.path;
      if (!oldPath) return;
      if (/[<>:"|?*\\]/.test(val) || val.includes("..")) {
        setValidationResult({ valid: false, errors: [`Invalid filename: ${val}`], warnings: [] });
        return;
      }
      const parts = oldPath.split("/");
      parts[parts.length - 1] = val;
      const newPath = parts.join("/");
      if (newPath !== oldPath && (virtualFiles.includes(newPath) || editor.pendingCreates.has(newPath))) {
        setValidationResult({ valid: false, errors: [`File already exists: ${newPath}`], warnings: [] });
        return;
      }
      editor.stageMove(oldPath, newPath);
    } else if (type === "move") {
      const oldPath = activeDialog?.context.path;
      if (!oldPath) return;
      const fileName = oldPath.split("/").pop()!;
      const newPath = val === "(root)" ? fileName : `${val}/${fileName}`;
      editor.stageMove(oldPath, newPath);
    } else if (type === "deleteFile") {
      const target = val;
      const isDir = target.startsWith("__dir__");
      if (isDir) {
        editor.stageDeleteDir(target.replace("__dir__", ""));
        if (currentPath.startsWith(target.replace("__dir__", "") + "/")) setSearchParams({});
      } else {
        editor.stageDelete(target);
        if (target === currentPath) setSearchParams({});
      }
    }
    setActiveDialog(null);
  }, [activeDialog, editor, virtualFiles, currentPath, setSearchParams]);

  const availableDirs = useMemo(() => {
    const dirs = new Set<string>(["(root)"]);
    for (const f of virtualFiles) { const idx = f.indexOf("/"); if (idx > 0) dirs.add(f.slice(0, idx)); }
    for (const path of editor.pendingCreates.keys()) { const idx = path.indexOf("/"); if (idx > 0) dirs.add(path.slice(0, idx)); }
    return [...dirs].sort();
  }, [virtualFiles, editor.pendingCreates, changedFiles]);

  const blocker = useUnsavedGuard({ hasChanges: hasPendingOps, onSave: handleSaveAll, saving: editor.saving });
  const assistantOpen = useSkillAssistantStore(s => s.panelOpen);

  const toggleAssistant = () => {
    if (!skillId) return;
    const store = useSkillAssistantStore.getState();
    if (store.panelOpen) store.closePanel();
    else store.openPanel(skillId);
  };

  if (!skill && !editor.loadingContent) {
    return <div className="flex items-center justify-center h-full text-gray-400">Skill not found</div>;
  }

  return (
    <div className="flex flex-col h-full">
      <SkillToolbar
        skill={skill}
        isAgentMode={storage.isAgentMode}
        hasPendingOps={hasPendingOps}
        pendingCount={pendingCount}
        saving={editor.saving}
        validating={validating}
        assistantOpen={assistantOpen}
        onBack={() => navigate(storage.isAgentMode ? `/agents/edit/${agentId}` : "/skills")}
        onSave={handleSaveAll}
        onDiscard={() => editor.handleDiscard()}
        onValidate={handleValidate}
        onShowDiff={() => setShowDiff(true)}
        onDelete={() => setShowDeleteConfirm(true)}
        onToggleAssistant={toggleAssistant}
      />

      <ValidationBanner
        result={validationResult}
        onDismiss={() => setValidationResult(null)}
        onAutoFix={async () => {
          if (!skillId) return;
          const allFilesList = ["SKILL.md", ...virtualFiles];
          for (const filePath of allFilesList) {
            if (editor.getEditedContent(filePath) !== undefined || editor.getOriginalContent(filePath) !== undefined) continue;
            const content = filePath === "SKILL.md" ? await storage.getContent() : await storage.getFile(filePath);
            if (content !== null) editor.setEditedContent(filePath, content);
          }
          const store = useSkillAssistantStore.getState();
          if (!store.panelOpen) store.openPanel(skillId);
          const issues = [...(validationResult?.errors ?? []), ...(validationResult?.warnings ?? [])].join("\n");
          store.sendMessage(
            `## Auto-Fix Task\nFix ONLY the following validation issues. Do NOT remove or rewrite any existing content.\n\nIssues:\n${issues}\n\nRules:\n- Use __file_content (4 backticks) to output the COMPLETE fixed file.\n- Fix ONLY the specific issues listed above.\n- NEVER delete existing content, sections, or descriptions.\n- NEVER shorten or summarize existing text.\n- If an issue appears already fixed in the current file content, skip it and say so.\n- Do NOT ask for confirmation. Execute fixes immediately.`,
            { path: currentPath, content: skillContent ?? "", allFiles: allFilesList, getFileContent: (p: string) => editor.getEditedContent(p) ?? editor.getOriginalContent(p) ?? null },
            (path: string, newContent: string) => editor.markNewFromExternal(path, newContent),
          );
          setValidationResult(null);
        }}
      />

      <div className="flex flex-1 overflow-hidden">
        <SkillFileTree
          treeData={treeData}
          currentFile={currentPath}
          changedFiles={changedFiles}
          pendingDeletes={pendingDeletes}
          pendingDeleteDirs={pendingDeleteDirs}
          pendingCreates={editor.pendingCreates}
          sidebarWidth={sidebarWidth}
          dragHandleProps={sidebarDragProps}
          onSelectFile={handleNodeClick}
          onNewFile={(parentDir) => setActiveDialog({ type: "newFile", context: { parentDir } })}
          onNewFolder={(parentDir) => setActiveDialog({ type: "newFolder", context: { parentDir } })}
          onRename={(path, currentName) => setActiveDialog({ type: "rename", context: { path, currentName } })}
          onMove={(path) => setActiveDialog({ type: "move", context: { path } })}
          onDeleteFile={(path) => setActiveDialog({ type: "deleteFile", context: { path } })}
          onStageMove={(oldPath, newPath) => editor.stageMove(oldPath, newPath)}
        />

        <div className="flex-1 min-w-0 overflow-hidden flex flex-col">
          <SkillEditorPane
            content={skillContent}
            currentPath={currentPath}
            onChange={handleEditorChange}
            loadingContent={editor.loadingContent}
          />
        </div>

        {skillId && (
          <SkillAssistant
            skillId={skillId}
            currentPath={currentPath}
            currentContent={skillContent ?? ""}
            allFiles={["SKILL.md", ...virtualFiles]}
            getFileContent={(path) => editor.getEditedContent(path) ?? editor.getOriginalContent(path) ?? null}
            onFileUpdate={(path, newContent) => editor.markNewFromExternal(path, newContent)}
          />
        )}
      </div>

      {showDiff && <DiffModal changes={editor.getDiffChanges()} onClose={() => setShowDiff(false)} />}

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

      <SkillFileDialogs
        activeDialog={activeDialog}
        availableDirs={availableDirs}
        onClose={() => setActiveDialog(null)}
        onConfirm={handleDialogConfirm}
      />
    </div>
  );
}
