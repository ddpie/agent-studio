import { useState, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { FolderPlus, Plus, Pencil, ArrowRightLeft, Trash2, ChevronLeft, ChevronRight as ChevronRightIcon } from "lucide-react";
import { Tree, type NodeRendererProps } from "react-arborist";
import { getFileIcon, type TreeNode } from "../../lib/tree-helpers";
import useIsDark from "../../hooks/useIsDark";

interface SkillFileTreeProps {
  treeData: TreeNode[];
  currentFile: string;
  changedFiles: Set<string>;
  pendingDeletes: Set<string>;
  pendingDeleteDirs: Set<string>;
  pendingCreates: Map<string, string>;
  sidebarWidth: number;
  dragHandleProps: { onMouseDown: (e: React.MouseEvent) => void; onDoubleClick: () => void };
  onSelectFile: (path: string) => void;
  onNewFile: (parentDir: string) => void;
  onNewFolder: (parentDir: string) => void;
  onRename: (path: string, currentName: string) => void;
  onMove: (path: string) => void;
  onDeleteFile: (path: string) => void;
  onStageMove: (oldPath: string, newPath: string) => void;
}

export default function SkillFileTree({
  treeData, currentFile, changedFiles, pendingDeletes, pendingDeleteDirs, pendingCreates,
  sidebarWidth, dragHandleProps, onSelectFile, onNewFile, onNewFolder, onRename, onMove, onDeleteFile, onStageMove,
}: SkillFileTreeProps) {
  const { t } = useTranslation();
  const isDark = useIsDark();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string; isFolder: boolean } | null>(null);
  const [treeHeight, setTreeHeight] = useState(400);
  const treeContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = treeContainerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setTreeHeight(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [contextMenu]);

  const handleContextMenu = (e: React.MouseEvent, nodeId: string, isFolder: boolean) => {
    e.preventDefault();
    e.stopPropagation();
    if (nodeId === "SKILL.md") return;
    setContextMenu({ x: e.clientX, y: e.clientY, nodeId, isFolder });
  };

  const FileNode = useCallback(({ node, style }: NodeRendererProps<TreeNode>) => {
    const isFolder = node.isInternal;
    const isActive = !isFolder && node.id === currentFile;
    const isChanged = changedFiles.has(node.id);
    const isDeleted = pendingDeletes.has(node.id) || (isFolder && pendingDeleteDirs.has(node.id.replace("__dir__", "")));
    const isNew = pendingCreates.has(node.id);
    const canContextMenu = node.id !== "SKILL.md";

    return (
      <div
        style={style}
        className={`group flex items-center gap-1.5 px-2 py-0.5 cursor-pointer select-none text-xs rounded-sm mx-1
          ${isDeleted ? isDark ? "text-gray-600 line-through" : "text-gray-400 line-through"
            : isActive ? isDark ? "bg-blue-600/20 text-blue-400" : "bg-blue-100 text-blue-700"
            : isNew ? isDark ? "text-green-400" : "text-green-600"
            : isDark ? "text-gray-300 hover:bg-gray-700/50" : "text-gray-700 hover:bg-gray-200/50"
          }`}
        onClick={() => {
          if (isDeleted) return;
          if (isFolder) node.toggle();
          else onSelectFile(node.id);
        }}
        onContextMenu={canContextMenu && !isDeleted ? (e) => handleContextMenu(e, node.id, isFolder) : undefined}
      >
        {isFolder && <ChevronRightIcon className={`w-3 h-3 ${isDark ? "text-gray-500" : "text-gray-400"} transition-transform ${node.isOpen ? "rotate-90" : ""}`} />}
        {getFileIcon(node.data.name, isFolder, node.isOpen)}
        <span className="truncate flex-1">{node.data.name}</span>
        {isNew && <span className="text-[9px] text-green-500 font-medium">{t("skillEditor.newTag")}</span>}
        {isDeleted && <span className="text-[9px] text-red-400 font-medium">{t("skillEditor.delTag")}</span>}
        {isChanged && !isDeleted && !isNew && <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />}
      </div>
    );
  }, [currentFile, changedFiles, isDark, pendingDeletes, pendingCreates]);

  return (
    <>
      {!sidebarCollapsed && (
        <div style={{ width: sidebarWidth }} className={`border-r overflow-hidden flex-shrink-0 flex flex-col ${isDark ? "border-gray-700 bg-[#252526]" : "border-gray-200 bg-gray-50"}`}>
          <div className={`px-3 py-2 flex items-center justify-between ${isDark ? "text-gray-500" : "text-gray-400"}`}>
            <span className="text-[10px] font-semibold uppercase tracking-wider">Files</span>
            <div className="flex items-center gap-0.5">
              <button onClick={() => onNewFile("")} className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`} title={t("skillEditor.newFile")}>
                <Plus className="w-3.5 h-3.5" />
              </button>
              <button onClick={() => onNewFolder("")} className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`} title={t("skillEditor.newFolder")}>
                <FolderPlus className="w-3.5 h-3.5" />
              </button>
              <button onClick={() => setSidebarCollapsed(true)} className={`p-0.5 rounded ${isDark ? "hover:bg-gray-600 text-gray-500 hover:text-gray-300" : "hover:bg-gray-200 text-gray-400 hover:text-gray-600"}`} title={t("skillEditor.hideSidebar")}>
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
              disableDrop={(args) => !!(args.parentNode && !args.parentNode.isInternal)}
              onMove={({ dragIds, parentId }) => {
                for (const id of dragIds) {
                  if (id === "SKILL.md" || id.startsWith("__dir__")) continue;
                  const fileName = id.split("/").pop()!;
                  const newDir = parentId?.replace("__dir__", "") ?? "";
                  const newPath = newDir ? `${newDir}/${fileName}` : fileName;
                  onStageMove(id, newPath);
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
      {!sidebarCollapsed && (
        <div {...dragHandleProps} className="w-1 cursor-col-resize bg-transparent hover:bg-blue-400/30 active:bg-blue-400/50 flex-shrink-0 transition-colors" title="Drag to resize" />
      )}

      {/* Context menu */}
      {contextMenu && (
        <div style={{ position: "fixed", left: contextMenu.x, top: contextMenu.y, zIndex: 60 }}
          className={`${isDark ? "bg-gray-800 border-gray-700" : "bg-white border-gray-200"} border rounded-lg shadow-xl py-1 min-w-[140px]`}>
          {contextMenu.isFolder && (
            <>
              <button onClick={() => { onNewFile(contextMenu.nodeId.replace("__dir__", "")); setContextMenu(null); }}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
                <Plus className="w-3 h-3" /> {t("skillEditor.newFileRoot")}
              </button>
              <button onClick={() => { onNewFolder(contextMenu.nodeId.replace("__dir__", "")); setContextMenu(null); }}
                className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
                <FolderPlus className="w-3 h-3" /> {t("skillEditor.newFolderTitle")}
              </button>
            </>
          )}
          {!contextMenu.isFolder && (
            <button onClick={() => { onRename(contextMenu.nodeId, contextMenu.nodeId.split("/").pop()!); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <Pencil className="w-3 h-3" /> {t("skillEditor.renameFile")}
            </button>
          )}
          {!contextMenu.isFolder && (
            <button onClick={() => { onMove(contextMenu.nodeId); setContextMenu(null); }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 ${isDark ? "text-gray-300 hover:bg-gray-700" : "text-gray-700 hover:bg-gray-100"}`}>
              <ArrowRightLeft className="w-3 h-3" /> {t("skillEditor.moveTo")}
            </button>
          )}
          <button onClick={() => { onDeleteFile(contextMenu.nodeId); setContextMenu(null); }}
            className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 text-red-400 ${isDark ? "hover:bg-gray-700" : "hover:bg-red-50"}`}>
            <Trash2 className="w-3 h-3" /> {t("common.delete")}
          </button>
        </div>
      )}
    </>
  );
}
