/**
 * Tree data helpers for skill file tree (react-arborist).
 */
import { FileText, FolderOpen, FolderClosed, File } from "lucide-react";
import { createElement } from "react";

export type TreeNode = {
  id: string;
  name: string;
  children?: TreeNode[];
};

export function buildTreeData(files: string[]): TreeNode[] {
  const root: TreeNode[] = [{ id: "SKILL.md", name: "SKILL.md" }];

  interface DirEntry { children: Map<string, DirEntry>; files: { id: string; name: string }[] }
  const rootDir: DirEntry = { children: new Map(), files: [] };

  for (const f of files) {
    if (f === "SKILL.md") continue; // already in root
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
    for (const [name, sub] of [...dir.children.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      const dirPath = prefix ? `${prefix}/${name}` : name;
      nodes.push({
        id: `__dir__${dirPath}`,
        name,
        children: buildLevel(sub, dirPath),
      });
    }
    for (const f of dir.files.sort((a, b) => a.name.localeCompare(b.name))) {
      nodes.push({ id: f.id, name: f.name });
    }
    return nodes;
  }

  root.push(...buildLevel(rootDir, ""));
  return root;
}

export function getFileIcon(name: string, isFolder: boolean, isOpen: boolean) {
  if (isFolder) return isOpen
    ? createElement(FolderOpen, { className: "w-3.5 h-3.5 text-yellow-500" })
    : createElement(FolderClosed, { className: "w-3.5 h-3.5 text-yellow-500" });
  if (name.endsWith(".md")) return createElement(FileText, { className: "w-3.5 h-3.5 text-blue-400" });
  if (name.endsWith(".py")) return createElement(File, { className: "w-3.5 h-3.5 text-green-400" });
  if (name.endsWith(".json")) return createElement(File, { className: "w-3.5 h-3.5 text-yellow-400" });
  if (name.endsWith(".js") || name.endsWith(".ts")) return createElement(File, { className: "w-3.5 h-3.5 text-amber-400" });
  return createElement(File, { className: "w-3.5 h-3.5 text-gray-400" });
}
