import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock api-client + skill-storage — must come before SUT import.
const fetchAgentSkillFiles = vi.fn();
const fetchAgentSkillFile = vi.fn();
const putAgentSkillFile = vi.fn();
const deleteAgentSkillFiles = vi.fn();

vi.mock("../api-client", () => ({
  fetchAgentSkillFiles: (...a: unknown[]) => fetchAgentSkillFiles(...a),
  fetchAgentSkillFile: (...a: unknown[]) => fetchAgentSkillFile(...a),
  putAgentSkillFile: (...a: unknown[]) => putAgentSkillFile(...a),
  deleteAgentSkillFiles: (...a: unknown[]) => deleteAgentSkillFiles(...a),
}));

const getSkillFile = vi.fn();
const listSkillFiles = vi.fn();

vi.mock("../skill-storage", () => ({
  getSkillFile: (...a: unknown[]) => getSkillFile(...a),
  listSkillFiles: (...a: unknown[]) => listSkillFiles(...a),
}));

import {
  computeSkillHash,
  copySkillToAgent,
  readAgentSkillFile,
  writeAgentSkillFile,
  deleteAgentSkill,
  listAgentSkillFiles,
  readGlobalSkillFiles,
  readAllAgentSkillFiles,
} from "../agent-skill-storage";
import type { SkillIndexEntry } from "../skill-storage";

beforeEach(() => {
  fetchAgentSkillFiles.mockReset();
  fetchAgentSkillFile.mockReset();
  putAgentSkillFile.mockReset();
  deleteAgentSkillFiles.mockReset();
  getSkillFile.mockReset();
  listSkillFiles.mockReset();
});

const sampleEntry: SkillIndexEntry = {
  id: "src-1",
  name: "demo",
  description: "demo desc",
  contentHash: "deadbeef",
};

describe("computeSkillHash", () => {
  it("returns 8 lowercase hex chars", async () => {
    const h = await computeSkillHash({ "SKILL.md": "x" });
    expect(h).toMatch(/^[0-9a-f]{8}$/);
  });

  it("is deterministic for identical inputs", async () => {
    const a = await computeSkillHash({ "SKILL.md": "hi", "x.txt": "y" });
    const b = await computeSkillHash({ "x.txt": "y", "SKILL.md": "hi" });
    // Sorting key order shouldn't matter.
    expect(a).toBe(b);
  });

  it("changes when a file's content changes", async () => {
    const a = await computeSkillHash({ "SKILL.md": "v1" });
    const b = await computeSkillHash({ "SKILL.md": "v2" });
    expect(a).not.toBe(b);
  });

  it("handles empty file map", async () => {
    const h = await computeSkillHash({});
    expect(h).toMatch(/^[0-9a-f]{8}$/);
  });
});

describe("simple wrappers", () => {
  it("readAgentSkillFile delegates to api-client", async () => {
    fetchAgentSkillFile.mockResolvedValue("body");
    await expect(readAgentSkillFile("a", "s", "f.txt")).resolves.toBe("body");
    expect(fetchAgentSkillFile).toHaveBeenCalledWith("a", "s", "f.txt");
  });

  it("writeAgentSkillFile delegates to api-client", async () => {
    putAgentSkillFile.mockResolvedValue(true);
    await expect(writeAgentSkillFile("a", "s", "f.txt", "c")).resolves.toBe(true);
    expect(putAgentSkillFile).toHaveBeenCalledWith("a", "s", "f.txt", "c");
  });

  it("deleteAgentSkill delegates to api-client", async () => {
    deleteAgentSkillFiles.mockResolvedValue(true);
    await expect(deleteAgentSkill("a", "s")).resolves.toBe(true);
    expect(deleteAgentSkillFiles).toHaveBeenCalledWith("a", "s");
  });

  it("listAgentSkillFiles delegates to api-client", async () => {
    fetchAgentSkillFiles.mockResolvedValue(["a.txt", "b.txt"]);
    await expect(listAgentSkillFiles("a", "s")).resolves.toEqual(["a.txt", "b.txt"]);
  });
});

describe("readAllAgentSkillFiles", () => {
  it("reads each listed file and returns map", async () => {
    fetchAgentSkillFiles.mockResolvedValue(["a.txt", "b.txt"]);
    fetchAgentSkillFile.mockImplementation(async (_a: string, _s: string, p: string) =>
      p === "a.txt" ? "AAA" : "BBB",
    );
    const out = await readAllAgentSkillFiles("agt", "sk");
    expect(out).toEqual({ "a.txt": "AAA", "b.txt": "BBB" });
  });

  it("skips files whose content read returns null", async () => {
    fetchAgentSkillFiles.mockResolvedValue(["a", "b"]);
    fetchAgentSkillFile.mockImplementation(async (_a: string, _s: string, p: string) =>
      p === "a" ? "AAA" : null,
    );
    const out = await readAllAgentSkillFiles("agt", "sk");
    expect(out).toEqual({ "a": "AAA" });
  });

  it("returns {} when listing is empty", async () => {
    fetchAgentSkillFiles.mockResolvedValue([]);
    const out = await readAllAgentSkillFiles("agt", "sk");
    expect(out).toEqual({});
  });
});

describe("copySkillToAgent", () => {
  it("reads SKILL.md + extras, writes them, returns AgentSkillEntry", async () => {
    getSkillFile.mockImplementation(async (_id: string, p: string) => {
      if (p === "SKILL.md") return "---\nname: demo\n---";
      if (p === "scripts/run.py") return "print(1)";
      if (p === "data.json") return "{}";
      return null;
    });
    listSkillFiles.mockResolvedValue(["scripts/run.py", "data.json"]);
    putAgentSkillFile.mockResolvedValue(true);

    const progressEvents: Array<[number, number, string]> = [];
    const entry = await copySkillToAgent("agt", sampleEntry, (d, t, phase) =>
      progressEvents.push([d, t, phase]),
    );

    expect(entry.sourceSkillId).toBe("src-1");
    expect(entry.name).toBe("demo");
    expect(entry.description).toBe("demo desc");
    expect(entry.sourceContentHash).toBe("deadbeef");
    expect(entry.contentHash).toMatch(/^[0-9a-f]{8}$/);
    expect(entry.id).toMatch(/^[0-9a-f-]{8}$/);
    expect(entry.files).toEqual(["SKILL.md", "data.json", "scripts/run.py"]);
    // 3 puts (1 SKILL.md + 2 extras)
    expect(putAgentSkillFile).toHaveBeenCalledTimes(3);
    // Progress callbacks fired for both phases.
    expect(progressEvents.some(e => e[2] === "read")).toBe(true);
    expect(progressEvents.some(e => e[2] === "write")).toBe(true);
  });

  it("throws when any write fails and rolls back successful writes", async () => {
    getSkillFile.mockImplementation(async (_id: string, p: string) =>
      p === "SKILL.md" ? "---\nname: x\n---" : "ok",
    );
    listSkillFiles.mockResolvedValue(["bad.txt", "good.txt"]);
    putAgentSkillFile.mockImplementation(async (_a: string, _s: string, path: string) =>
      path !== "bad.txt",
    );
    deleteAgentSkillFiles.mockResolvedValue(true);

    await expect(copySkillToAgent("agt", sampleEntry)).rejects.toThrow(/Failed to write/);
    // Cleanup deletes called for the writes that did succeed.
    const cleanedPaths = deleteAgentSkillFiles.mock.calls.map(c => c[2]);
    expect(cleanedPaths).toContain("SKILL.md");
    expect(cleanedPaths).toContain("good.txt");
    expect(cleanedPaths).not.toContain("bad.txt");
  });

  it("falls back contentHash when source skill has no contentHash", async () => {
    getSkillFile.mockResolvedValue("---\nname: x\n---");
    listSkillFiles.mockResolvedValue([]);
    putAgentSkillFile.mockResolvedValue(true);
    const noHash: SkillIndexEntry = { ...sampleEntry, contentHash: undefined };
    const entry = await copySkillToAgent("agt", noHash);
    // sourceContentHash should equal own contentHash when source had none.
    expect(entry.sourceContentHash).toBe(entry.contentHash);
  });

  it("skips SKILL.md slot when global read returns null", async () => {
    getSkillFile.mockImplementation(async (_id: string, p: string) =>
      p === "SKILL.md" ? null : "x",
    );
    listSkillFiles.mockResolvedValue(["a.txt"]);
    putAgentSkillFile.mockResolvedValue(true);
    const entry = await copySkillToAgent("agt", sampleEntry);
    expect(entry.files).toEqual(["a.txt"]);
  });
});

describe("readGlobalSkillFiles", () => {
  it("reads files from a global skill without writing", async () => {
    getSkillFile.mockImplementation(async (_id: string, p: string) =>
      p === "SKILL.md" ? "MD" : "FILE",
    );
    listSkillFiles.mockResolvedValue(["a.txt", "b.txt"]);
    const progress: Array<[number, number]> = [];
    const { entry, files } = await readGlobalSkillFiles(sampleEntry, (d, t) => progress.push([d, t]));
    expect(files).toEqual({ "SKILL.md": "MD", "a.txt": "FILE", "b.txt": "FILE" });
    expect(entry.sourceSkillId).toBe("src-1");
    expect(entry.contentHash).toMatch(/^[0-9a-f]{8}$/);
    expect(progress.length).toBeGreaterThan(0);
    // No writes should occur.
    expect(putAgentSkillFile).not.toHaveBeenCalled();
  });

  it("handles empty extras list", async () => {
    getSkillFile.mockImplementation(async (_id: string, p: string) =>
      p === "SKILL.md" ? "MD" : null,
    );
    listSkillFiles.mockResolvedValue([]);
    const { entry, files } = await readGlobalSkillFiles(sampleEntry);
    expect(files).toEqual({ "SKILL.md": "MD" });
    expect(entry.files).toEqual(["SKILL.md"]);
  });

  it("uses own hash when source has no contentHash", async () => {
    getSkillFile.mockResolvedValue(null);
    listSkillFiles.mockResolvedValue([]);
    const noHash = { ...sampleEntry, contentHash: undefined };
    const { entry } = await readGlobalSkillFiles(noHash);
    expect(entry.sourceContentHash).toBe(entry.contentHash);
  });
});
