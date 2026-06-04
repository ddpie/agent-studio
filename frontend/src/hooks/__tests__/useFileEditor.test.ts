/**
 * useFileEditor — multi-file editor state machine tests.
 * Covers add/delete/rename, dirty tracking, save flow, file switch, and
 * draft autosave/restore integration.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";

// --- Mock draft-autosave: keep behavior simple and observable -------------
const loadDraftWithMeta = vi.fn();
const clearDraft = vi.fn();
const debouncedSchedule = vi.fn();
const debouncedFlush = vi.fn();
const debouncedCancel = vi.fn();
const createDebouncedSaver = vi.fn(() => ({
  schedule: debouncedSchedule,
  flush: debouncedFlush,
  cancel: debouncedCancel,
}));

vi.mock("../../lib/draft-autosave", () => ({
  loadDraftWithMeta: (...args: unknown[]) => loadDraftWithMeta(...args),
  clearDraft: (...args: unknown[]) => clearDraft(...args),
  createDebouncedSaver: (...args: unknown[]) => createDebouncedSaver(...args),
}));

// --- Mock validators: by default everything is valid ---------------------
const validateSkill = vi.fn(() => ({ valid: true, errors: [], warnings: [] }));
vi.mock("../../lib/validators/skill-validator", () => ({
  validateSkill: (...args: unknown[]) => validateSkill(...args),
}));

import { useFileEditor } from "../useFileEditor";
import type { SkillStorageOps } from "../useSkillStorage";

// --- Storage mock factory -------------------------------------------------
function createStorageMock(overrides: Partial<SkillStorageOps> = {}): SkillStorageOps {
  return {
    getContent: vi.fn().mockResolvedValue("---\nname: my-skill\n---\nbody"),
    getFile: vi.fn().mockResolvedValue("file content"),
    listFiles: vi.fn().mockResolvedValue([]),
    writeFile: vi.fn().mockResolvedValue(true),
    deleteFile: vi.fn().mockResolvedValue(true),
    renameFile: vi.fn().mockResolvedValue(true),
    isAgentMode: false,
    storageSkillId: "skill-1",
    ...overrides,
  };
}

beforeEach(() => {
  loadDraftWithMeta.mockReset();
  loadDraftWithMeta.mockReturnValue(null);
  clearDraft.mockReset();
  debouncedSchedule.mockReset();
  debouncedFlush.mockReset();
  debouncedCancel.mockReset();
  createDebouncedSaver.mockClear();
  validateSkill.mockReset();
  validateSkill.mockReturnValue({ valid: true, errors: [], warnings: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useFileEditor — initial load", () => {
  it("loadInitial fetches content + file list and seeds originals", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: a\n---\nbody"),
      listFiles: vi.fn().mockResolvedValue(["scripts/a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));

    let loaded;
    await act(async () => {
      loaded = await result.current.loadInitial();
    });

    expect(loaded).toEqual({ files: ["scripts/a.py"], content: "---\nname: a\n---\nbody" });
    expect(result.current.files).toEqual(["scripts/a.py"]);
    expect(result.current.content).toBe("---\nname: a\n---\nbody");
    expect(result.current.currentFile).toBe("SKILL.md");
    expect(result.current.loadingContent).toBe(false);
    expect(result.current.getOriginalContent("SKILL.md")).toBe("---\nname: a\n---\nbody");
    expect(storage.getContent).toHaveBeenCalled();
    expect(storage.listFiles).toHaveBeenCalled();
  });

  it("loadInitial with explicit initialFile uses getFile", async () => {
    const storage = createStorageMock({
      getFile: vi.fn().mockResolvedValue("custom"),
      listFiles: vi.fn().mockResolvedValue(["scripts/a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => {
      await result.current.loadInitial("scripts/a.py");
    });
    expect(storage.getFile).toHaveBeenCalledWith("scripts/a.py");
    expect(result.current.currentFile).toBe("scripts/a.py");
    expect(result.current.getOriginalContent("scripts/a.py")).toBe("custom");
  });

  it("loadFromMemory marks every entry as a pending create", () => {
    const storage = createStorageMock();
    const { result } = renderHook(() => useFileEditor({ storage }));
    act(() => {
      result.current.loadFromMemory({
        "SKILL.md": "---\nname: x\n---\nbody",
        "scripts/a.py": "print(1)",
      });
    });
    expect(result.current.content).toBe("---\nname: x\n---\nbody");
    expect(result.current.currentFile).toBe("SKILL.md");
    expect(result.current.pendingCreates.size).toBe(2);
    expect(result.current.pendingCreates.get("scripts/a.py")).toBe("print(1)");
    expect(result.current.hasPendingOps).toBe(true);
  });
});

describe("useFileEditor — editor changes & dirty tracking", () => {
  it("handleEditorChange marks file dirty when content differs from original", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    act(() => { result.current.handleEditorChange("orig changed"); });
    expect(result.current.changedFiles.has("SKILL.md")).toBe(true);
    expect(result.current.content).toBe("orig changed");

    // Reverting back removes from dirty set
    act(() => { result.current.handleEditorChange("orig"); });
    expect(result.current.changedFiles.has("SKILL.md")).toBe(false);
  });

  it("handleEditorChange ignores undefined val", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange(undefined); });
    expect(result.current.content).toBe("orig");
  });

  it("markChanged adds to dirty set when edited != original", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("a"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.setEditedContent("SKILL.md", "b"); });
    act(() => { result.current.markChanged("SKILL.md"); });
    expect(result.current.changedFiles.has("SKILL.md")).toBe(true);
  });
});

describe("useFileEditor — staging operations", () => {
  it("stageNewFile adds a virtual file", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    let ok = false;
    act(() => { ok = result.current.stageNewFile("scripts/new.py", "print(1)"); });
    expect(ok).toBe(true);
    expect(result.current.virtualFiles).toContain("scripts/new.py");
    expect(result.current.pendingCreates.get("scripts/new.py")).toBe("print(1)");
    expect(result.current.changedFiles.has("scripts/new.py")).toBe(true);

    // duplicate path returns false
    let dup = true;
    act(() => { dup = result.current.stageNewFile("scripts/new.py", "x"); });
    expect(dup).toBe(false);
  });

  it("stageDelete on existing file adds to pendingDeletes; current file falls back to SKILL.md", async () => {
    const onFileSwitch = vi.fn();
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("md"),
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getFile: vi.fn().mockResolvedValue("py contents"),
    });
    const { result } = renderHook(() => useFileEditor({ storage, onFileSwitch }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.selectFile("a.py"); });
    expect(result.current.currentFile).toBe("a.py");

    act(() => { result.current.stageDelete("a.py"); });
    expect(result.current.pendingDeletes.has("a.py")).toBe(true);
    expect(result.current.currentFile).toBe("SKILL.md");
    expect(onFileSwitch).toHaveBeenCalledWith("SKILL.md");
  });

  it("stageDelete on a pending-create just removes the create", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageNewFile("scripts/new.py", "x"); });
    expect(result.current.pendingCreates.has("scripts/new.py")).toBe(true);

    act(() => { result.current.stageDelete("scripts/new.py"); });
    expect(result.current.pendingCreates.has("scripts/new.py")).toBe(false);
    expect(result.current.pendingDeletes.has("scripts/new.py")).toBe(false);
  });

  it("stageDeleteDir clears edits in that directory and resets current file", async () => {
    const onFileSwitch = vi.fn();
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("md"),
      listFiles: vi.fn().mockResolvedValue(["scripts/a.py", "scripts/b.py", "other.txt"]),
      getFile: vi.fn().mockResolvedValue("contents"),
    });
    const { result } = renderHook(() => useFileEditor({ storage, onFileSwitch }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.selectFile("scripts/a.py"); });

    act(() => { result.current.stageDeleteDir("scripts"); });
    expect(result.current.pendingDeleteDirs.has("scripts")).toBe(true);
    expect(result.current.currentFile).toBe("SKILL.md");
    expect(onFileSwitch).toHaveBeenCalledWith("SKILL.md");
  });

  it("stageMove on existing file records a rename and follows current file", async () => {
    const onFileSwitch = vi.fn();
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("md"),
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getFile: vi.fn().mockResolvedValue("py"),
    });
    const { result } = renderHook(() => useFileEditor({ storage, onFileSwitch }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.selectFile("a.py"); });

    act(() => { result.current.stageMove("a.py", "b.py"); });
    expect(result.current.pendingRenames.get("a.py")).toBe("b.py");
    expect(result.current.virtualFiles).toContain("b.py");
    expect(result.current.virtualFiles).not.toContain("a.py");
    expect(result.current.changedFiles.has("b.py")).toBe(true);
    expect(result.current.currentFile).toBe("b.py");
    expect(onFileSwitch).toHaveBeenCalledWith("b.py");
  });

  it("stageMove no-op when oldPath === newPath", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageMove("a.py", "a.py"); });
    expect(result.current.pendingRenames.size).toBe(0);
  });

  it("stageMove on a pending-create swaps the create entry", async () => {
    const storage = createStorageMock({ listFiles: vi.fn().mockResolvedValue([]) });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageNewFile("x.py", "code"); });
    act(() => { result.current.stageMove("x.py", "y.py"); });
    expect(result.current.pendingCreates.has("x.py")).toBe(false);
    expect(result.current.pendingCreates.get("y.py")).toBe("code");
    expect(result.current.pendingRenames.size).toBe(0);
  });

  it("stageMove back to original cancels the rename", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageMove("a.py", "b.py"); });
    expect(result.current.pendingRenames.get("a.py")).toBe("b.py");
    act(() => { result.current.stageMove("b.py", "a.py"); });
    expect(result.current.pendingRenames.has("a.py")).toBe(false);
  });
});

describe("useFileEditor — selectFile", () => {
  it("selectFile loads from storage on first access", async () => {
    const getFile = vi.fn().mockResolvedValue("py code");
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getFile,
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    await act(async () => { await result.current.selectFile("a.py"); });
    expect(result.current.currentFile).toBe("a.py");
    expect(result.current.content).toBe("py code");
    expect(getFile).toHaveBeenCalledWith("a.py");
    expect(result.current.getOriginalContent("a.py")).toBe("py code");
  });

  it("selectFile delegates to getContent for SKILL.md after switching away", async () => {
    const getContent = vi.fn().mockResolvedValue("md");
    const getFile = vi.fn().mockResolvedValue("py");
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getContent,
      getFile,
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.selectFile("a.py"); });
    // SKILL.md still flows through getContent
    await act(async () => { await result.current.selectFile("SKILL.md"); });
    // getContent invoked: 1 from loadInitial + 1 from selectFile("SKILL.md")
    expect(getContent).toHaveBeenCalledTimes(2);
  });

  it("selectFile prefers editedContents over storage", async () => {
    const getFile = vi.fn().mockResolvedValue("orig");
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getFile,
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.selectFile("a.py"); });
    act(() => { result.current.handleEditorChange("dirty edit"); });
    // Switch away then back
    await act(async () => { await result.current.selectFile("SKILL.md"); });
    await act(async () => { await result.current.selectFile("a.py"); });
    expect(result.current.content).toBe("dirty edit");
  });

  it("selectFile on a pending-create returns the staged content", async () => {
    const storage = createStorageMock({ listFiles: vi.fn().mockResolvedValue([]) });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageNewFile("scripts/new.py", "fresh"); });
    await act(async () => { await result.current.selectFile("scripts/new.py"); });
    expect(result.current.content).toBe("fresh");
  });
});

describe("useFileEditor — handleSaveAll", () => {
  it("returns success early when nothing pending", async () => {
    const storage = createStorageMock();
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    let res;
    await act(async () => { res = await result.current.handleSaveAll(); });
    expect(res).toEqual({ success: true, errors: [] });
    expect(storage.writeFile).not.toHaveBeenCalled();
  });

  it("fails fast when SKILL.md validation fails", async () => {
    validateSkill.mockReturnValueOnce({ valid: false, errors: ["bad"], warnings: [] });
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: x\n---\nbody"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("---\nname: x\n---\nchanged"); });

    let res;
    await act(async () => { res = await result.current.handleSaveAll(); });
    expect(res).toMatchObject({ success: false, errors: ["bad"] });
    expect(storage.writeFile).not.toHaveBeenCalled();
  });

  it("executes deletes, renames, creates and writes in order; refreshes from storage", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: x\n---\nbody"),
      listFiles: vi.fn()
        .mockResolvedValueOnce(["a.py", "b.py"])
        .mockResolvedValueOnce(["b.py", "new.py"]),
      getFile: vi.fn().mockResolvedValue("contents"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    // dirty SKILL.md
    act(() => { result.current.handleEditorChange("---\nname: x\n---\nbody changed"); });
    // delete a.py
    act(() => { result.current.stageDelete("a.py"); });
    // create new.py
    act(() => { result.current.stageNewFile("new.py", "print(2)"); });

    let res;
    await act(async () => { res = await result.current.handleSaveAll(); });

    expect(res?.success).toBe(true);
    expect(storage.deleteFile).toHaveBeenCalledWith("a.py");
    expect(storage.writeFile).toHaveBeenCalledWith("new.py", "print(2)");
    expect(storage.writeFile).toHaveBeenCalledWith("SKILL.md", "---\nname: x\n---\nbody changed");
    // Cleared after success
    expect(result.current.changedFiles.size).toBe(0);
    expect(result.current.pendingCreates.size).toBe(0);
    expect(result.current.pendingDeletes.size).toBe(0);
    expect(result.current.files).toEqual(["b.py", "new.py"]);
  });

  it("collects per-op errors and does not clear staging on failure", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: x\n---\nbody"),
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      deleteFile: vi.fn().mockRejectedValue(new Error("nope")),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageDelete("a.py"); });

    let res;
    await act(async () => { res = await result.current.handleSaveAll(); });
    expect(res?.success).toBe(false);
    expect(res?.errors[0]).toContain("Failed to delete a.py");
    // Pending state retained on failure
    expect(result.current.pendingDeletes.has("a.py")).toBe(true);
  });

  it("stageDeleteDir flush deletes every file under the dir", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: x\n---\nbody"),
      listFiles: vi.fn()
        .mockResolvedValueOnce(["scripts/a.py", "scripts/b.py", "other.txt"])
        .mockResolvedValueOnce(["other.txt"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.stageDeleteDir("scripts"); });

    let res;
    await act(async () => { res = await result.current.handleSaveAll(); });
    expect(res?.success).toBe(true);
    expect(storage.deleteFile).toHaveBeenCalledWith("scripts/a.py");
    expect(storage.deleteFile).toHaveBeenCalledWith("scripts/b.py");
  });
});

describe("useFileEditor — handleDiscard", () => {
  it("clears all pending state and restores current file content", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("dirty"); });
    act(() => { result.current.stageNewFile("a.py", "x"); });

    expect(result.current.hasPendingOps).toBe(true);
    act(() => { result.current.handleDiscard(); });
    expect(result.current.hasPendingOps).toBe(false);
    expect(result.current.content).toBe("orig");
  });
});

describe("useFileEditor — getDiffChanges", () => {
  it("includes edits, creates, deletes", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
      listFiles: vi.fn().mockResolvedValue(["b.py"]),
      getFile: vi.fn().mockResolvedValue("body"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("changed"); });
    act(() => { result.current.stageNewFile("new.py", "n"); });
    act(() => { result.current.stageDelete("b.py"); });

    const diff = result.current.getDiffChanges();
    expect(diff.has("SKILL.md")).toBe(true);
    expect(diff.has("new.py (new)")).toBe(true);
    expect(diff.has("b.py (deleted)")).toBe(true);
  });
});

describe("useFileEditor — restoreEdits", () => {
  it("restores edits and updates current file content", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });

    act(() => {
      result.current.restoreEdits({
        "SKILL.md": "restored skill",
        "a.py": "restored py",
      });
    });
    expect(result.current.changedFiles.has("SKILL.md")).toBe(true);
    expect(result.current.changedFiles.has("a.py")).toBe(true);
    expect(result.current.content).toBe("restored skill");
    expect(result.current.getEditedContent("a.py")).toBe("restored py");
  });
});

describe("useFileEditor — markNewFromExternal", () => {
  it("treats unknown path as new file (adds to pendingCreates)", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.markNewFromExternal("brand-new.py", "code"); });
    expect(result.current.pendingCreates.has("brand-new.py")).toBe(true);
    expect(result.current.changedFiles.has("brand-new.py")).toBe(true);
  });

  it("matches trimmed original — not marked dirty", async () => {
    const getFile = vi.fn().mockResolvedValue("hello\n");
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
      getFile,
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    await act(async () => { await result.current.markNewFromExternal("a.py", "hello"); });
    expect(result.current.changedFiles.has("a.py")).toBe(false);
  });
});

describe("useFileEditor — draft autosave/restore", () => {
  it("schedules a draft write on dirty change when draftKey is set", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
    });
    const { result } = renderHook(() =>
      useFileEditor({ storage, draftKey: "skill-draft:1" }),
    );
    await act(async () => { await result.current.loadInitial(); });
    expect(createDebouncedSaver).toHaveBeenCalledWith("skill-draft:1", 500);

    act(() => { result.current.handleEditorChange("dirty"); });
    expect(debouncedSchedule).toHaveBeenCalledWith({ "SKILL.md": "dirty" });
  });

  it("clears draft when all dirty edits are reverted", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
    });
    const { result } = renderHook(() =>
      useFileEditor({ storage, draftKey: "skill-draft:2" }),
    );
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("dirty"); });
    act(() => { result.current.handleEditorChange("orig"); });
    expect(debouncedCancel).toHaveBeenCalled();
    expect(clearDraft).toHaveBeenCalledWith("skill-draft:2");
  });

  it("restores a saved draft on loadInitial and notifies caller", async () => {
    loadDraftWithMeta.mockReturnValue({
      data: { "SKILL.md": "draft body", "stale.py": "ignore me" },
      ts: 1234567890,
    });
    const onDraftRestored = vi.fn();
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig body"),
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() =>
      useFileEditor({ storage, draftKey: "skill-draft:3", onDraftRestored }),
    );

    await act(async () => { await result.current.loadInitial(); });
    expect(onDraftRestored).toHaveBeenCalledWith(1234567890);
    expect(result.current.content).toBe("draft body");
    expect(result.current.changedFiles.has("SKILL.md")).toBe(true);
    // stale.py is filtered out (not in file list)
    expect(result.current.changedFiles.has("stale.py")).toBe(false);
  });

  it("save success clears the draft", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("---\nname: x\n---\nbody"),
      listFiles: vi.fn().mockResolvedValue([]),
    });
    const { result } = renderHook(() =>
      useFileEditor({ storage, draftKey: "skill-draft:save" }),
    );
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("---\nname: x\n---\nedited"); });

    await act(async () => { await result.current.handleSaveAll(); });
    expect(clearDraft).toHaveBeenCalledWith("skill-draft:save");
    expect(debouncedCancel).toHaveBeenCalled();
  });

  it("discard cancels and clears draft", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
    });
    const { result } = renderHook(() =>
      useFileEditor({ storage, draftKey: "skill-draft:disc" }),
    );
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("dirty"); });
    act(() => { result.current.handleDiscard(); });
    expect(debouncedCancel).toHaveBeenCalled();
    expect(clearDraft).toHaveBeenCalledWith("skill-draft:disc");
  });
});

describe("useFileEditor — accessors", () => {
  it("setFiles / setContent / setCurrentFile work as escape hatches", () => {
    const storage = createStorageMock();
    const { result } = renderHook(() => useFileEditor({ storage }));
    act(() => { result.current.setFiles(["x.py"]); });
    expect(result.current.files).toEqual(["x.py"]);
    act(() => { result.current.setContent("hi"); });
    expect(result.current.content).toBe("hi");
    act(() => { result.current.setCurrentFile("x.py"); });
    expect(result.current.currentFile).toBe("x.py");
  });

  it("getEditedContent returns staged edit", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("staged"); });
    expect(result.current.getEditedContent("SKILL.md")).toBe("staged");
  });

  it("treeData reflects virtualFiles", async () => {
    const storage = createStorageMock({
      listFiles: vi.fn().mockResolvedValue(["scripts/a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    expect(Array.isArray(result.current.treeData)).toBe(true);
    expect(result.current.treeData.length).toBeGreaterThan(0);
  });

  it("pendingCount aggregates all staging buckets", async () => {
    const storage = createStorageMock({
      getContent: vi.fn().mockResolvedValue("orig"),
      listFiles: vi.fn().mockResolvedValue(["a.py"]),
    });
    const { result } = renderHook(() => useFileEditor({ storage }));
    await act(async () => { await result.current.loadInitial(); });
    act(() => { result.current.handleEditorChange("dirty"); });
    act(() => { result.current.stageNewFile("new.py", "n"); });
    // changedFiles: SKILL.md + new.py (stageNewFile adds path to changedFiles too)
    // pendingCreates: new.py
    expect(result.current.pendingCount).toBe(3);
    expect(result.current.hasPendingOps).toBe(true);
  });
});
