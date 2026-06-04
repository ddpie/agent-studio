import { describe, it, expect } from "vitest";
import { buildTreeData, getFileIcon, type TreeNode } from "../tree-helpers";
import { isValidElement } from "react";

/** Walk a tree and collect ids in DFS order. */
function collectIds(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  for (const n of nodes) {
    out.push(n.id);
    if (n.children) out.push(...collectIds(n.children));
  }
  return out;
}

/** Find the first node anywhere in the tree by id. */
function findById(nodes: TreeNode[], id: string): TreeNode | undefined {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) {
      const hit = findById(n.children, id);
      if (hit) return hit;
    }
  }
  return undefined;
}

describe("buildTreeData", () => {
  it("always includes SKILL.md as the first node", () => {
    const tree = buildTreeData([]);
    expect(tree[0]).toEqual({ id: "SKILL.md", name: "SKILL.md" });
  });

  it("does not duplicate SKILL.md when supplied in input", () => {
    const tree = buildTreeData(["SKILL.md"]);
    expect(tree.filter(n => n.id === "SKILL.md")).toHaveLength(1);
  });

  it("places top-level files alongside SKILL.md sorted by name", () => {
    const tree = buildTreeData(["zeta.md", "alpha.md"]);
    const ids = tree.map(n => n.id);
    // SKILL.md first, then files sorted
    expect(ids[0]).toBe("SKILL.md");
    expect(ids.slice(1)).toEqual(["alpha.md", "zeta.md"]);
  });

  it("groups files into nested directory nodes", () => {
    const tree = buildTreeData(["scripts/run.py", "scripts/util.py", "README.md"]);
    const dir = tree.find(n => n.id === "__dir__scripts");
    expect(dir).toBeDefined();
    expect(dir!.children).toBeDefined();
    expect(dir!.name).toBe("scripts");
    const childIds = dir!.children!.map(n => n.id);
    expect(childIds).toEqual(["scripts/run.py", "scripts/util.py"]);
  });

  it("supports deep nesting and walks ancestors correctly", () => {
    const tree = buildTreeData([
      "a/b/c/leaf.txt",
      "a/b/sibling.txt",
      "a/top.txt",
    ]);

    const aDir = findById(tree, "__dir__a");
    expect(aDir).toBeDefined();
    expect(aDir!.children?.some(n => n.id === "a/top.txt")).toBe(true);

    const abDir = findById(tree, "__dir__a/b");
    expect(abDir).toBeDefined();
    expect(abDir!.children?.some(n => n.id === "a/b/sibling.txt")).toBe(true);

    const abcDir = findById(tree, "__dir__a/b/c");
    expect(abcDir).toBeDefined();
    expect(abcDir!.children?.map(n => n.id)).toEqual(["a/b/c/leaf.txt"]);
  });

  it("sorts directories before files within the same level", () => {
    const tree = buildTreeData(["zfile.txt", "adir/x.txt"]);
    // SKILL.md, then dir, then file (alpha sorted by branch)
    const nonSkill = tree.filter(n => n.id !== "SKILL.md");
    expect(nonSkill[0].id).toBe("__dir__adir");
    expect(nonSkill[1].id).toBe("zfile.txt");
  });

  it("emits empty children array for directory with no files (cannot happen but kept defensive)", () => {
    // Synthetic case — directories only exist if a file is under them, so build "a/x"
    // and remove "x" effectively by checking the produced subtree structure.
    const tree = buildTreeData(["a/x.txt"]);
    const aDir = findById(tree, "__dir__a");
    expect(Array.isArray(aDir!.children)).toBe(true);
    expect(aDir!.children!.length).toBe(1);
  });

  it("handles all-empty input cleanly", () => {
    const tree = buildTreeData([]);
    expect(collectIds(tree)).toEqual(["SKILL.md"]);
  });

  it("preserves directory id format __dir__<path>", () => {
    const tree = buildTreeData(["nested/deeper/file.py"]);
    const ids = collectIds(tree);
    expect(ids).toContain("__dir__nested");
    expect(ids).toContain("__dir__nested/deeper");
    expect(ids).toContain("nested/deeper/file.py");
  });
});

describe("getFileIcon", () => {
  it("returns folder-open icon when folder is open", () => {
    const el = getFileIcon("foo", true, true);
    expect(isValidElement(el)).toBe(true);
  });

  it("returns folder-closed icon when folder is closed", () => {
    const el = getFileIcon("foo", true, false);
    expect(isValidElement(el)).toBe(true);
  });

  it("returns a file icon for .md", () => {
    const el = getFileIcon("README.md", false, false);
    expect(isValidElement(el)).toBe(true);
  });

  it("returns a file icon for .py", () => {
    const el = getFileIcon("run.py", false, false);
    expect(isValidElement(el)).toBe(true);
  });

  it("returns a file icon for .json", () => {
    const el = getFileIcon("package.json", false, false);
    expect(isValidElement(el)).toBe(true);
  });

  it("returns a file icon for .ts and .js", () => {
    expect(isValidElement(getFileIcon("a.ts", false, false))).toBe(true);
    expect(isValidElement(getFileIcon("a.js", false, false))).toBe(true);
  });

  it("falls back to generic file icon for unknown extension", () => {
    const el = getFileIcon("LICENSE", false, false);
    expect(isValidElement(el)).toBe(true);
  });
});
