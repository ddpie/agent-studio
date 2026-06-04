import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ── Mocks ─────────────────────────────────────────────────────────────
//
// agent-edit-store reaches out to several network/storage modules. We
// stub them all so the store's pure logic (dirty tracking, skill add /
// remove, diff computation, skill-file pending state) can be exercised
// without touching real services.

vi.mock("../../lib/agent-metadata", async () => {
  const actual = await vi.importActual<object>("../../lib/agent-metadata");
  return {
    ...actual,
    fetchAgentMetadata: vi.fn(),
  };
});

vi.mock("../../lib/tool-extractor", () => ({
  extractToolsFromDeployment: vi.fn().mockResolvedValue(null),
}));

vi.mock("../../lib/api-client", () => ({
  fetchTools: vi.fn().mockResolvedValue({ items: [] }),
  deleteAgentSkillFiles: vi.fn().mockResolvedValue(undefined),
  fetchAgentSkillFilesBulk: vi.fn().mockResolvedValue({}),
}));

// Replace the debounced saver with a synchronous spy so subscribe()
// side effects don't try to write to localStorage on a real timer.
vi.mock("../../lib/draft-autosave", () => {
  const schedule = vi.fn();
  const cancel = vi.fn();
  const flush = vi.fn();
  return {
    createDebouncedSaver: vi.fn(() => ({ schedule, cancel, flush })),
    loadDraftWithMeta: vi.fn(() => null),
    clearDraft: vi.fn(),
    __spies: { schedule, cancel, flush },
  };
});

import { useAgentEditStore, cancelAllAgentDraftSavers } from "../agent-edit-store";
import { fetchAgentMetadata, type AgentMetadata, type AgentSkillEntry } from "../../lib/agent-metadata";
import { extractToolsFromDeployment } from "../../lib/tool-extractor";
import { fetchTools, deleteAgentSkillFiles, fetchAgentSkillFilesBulk } from "../../lib/api-client";
import { loadDraftWithMeta, clearDraft } from "../../lib/draft-autosave";

const mockFetchMeta = vi.mocked(fetchAgentMetadata);
const mockExtract = vi.mocked(extractToolsFromDeployment);
const mockFetchTools = vi.mocked(fetchTools);
const mockDeleteFiles = vi.mocked(deleteAgentSkillFiles);
const mockFetchBulk = vi.mocked(fetchAgentSkillFilesBulk);
const mockLoadDraft = vi.mocked(loadDraftWithMeta);
const mockClearDraft = vi.mocked(clearDraft);

function fullMetadata(overrides: Partial<AgentMetadata> = {}): AgentMetadata {
  return {
    name: "agent-1",
    display_name: "Agent One",
    description: "demo",
    model_id: "claude-3-7",
    default_model_id: "claude-3-7",
    system_prompt: "you are an agent",
    tool_definitions: "",
    tool_names: "",
    welcome_message: "hi",
    suggestions: [],
    tools: [],
    template_id: "",
    supports_images: false,
    created_at: "2026-01-01",
    updated_at: "2026-01-01",
    created_by: "user",
    visibility: "private",
    skills: [],
    mcp_targets: [],
    linked_agents: [],
    knowledge_bases: [],
    runtime_type: "zip",
    ...overrides,
  };
}

function makeSkill(overrides: Partial<AgentSkillEntry> = {}): AgentSkillEntry {
  return {
    id: "skill-1",
    sourceSkillId: "src-1",
    sourceContentHash: "hash-1",
    name: "skill-name",
    description: "desc",
    contentHash: "hash-1",
    files: ["SKILL.md"],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchTools.mockResolvedValue({ items: [] });
  mockExtract.mockResolvedValue(null);
  mockLoadDraft.mockReturnValue(null);
  mockFetchBulk.mockResolvedValue({});

  // Reset store data to initial values. Use partial setState (no replace) so
  // the action functions defined by `create<...>(...)` stay attached — using
  // replace=true would strip them and leave the store unusable.
  useAgentEditStore.setState({
    agentId: null,
    agentName: null,
    formData: null,
    originalData: null,
    loading: false,
    saving: false,
    pendingSkillFiles: {},
    originalSkillFiles: {},
    editingSkillId: null,
    restoredDraft: null,
  });
});

afterEach(() => {
  // Drop saver registry between tests so subscribe-driven scheduling state
  // doesn't bleed across cases.
  cancelAllAgentDraftSavers();
});

describe("agent-edit-store: openNewWithData / updateField", () => {
  it("openNewWithData 初始化 draft id, 设置 formData 并保留 skills 默认值", () => {
    useAgentEditStore.getState().openNewWithData({ name: "new-bot", display_name: "New Bot" });
    const state = useAgentEditStore.getState();

    expect(state.agentId).toMatch(/^draft-/);
    expect(state.agentName).toBe("New Bot");
    expect(state.formData?.name).toBe("new-bot");
    expect(state.formData?.skills).toEqual([]);
    expect(state.loading).toBe(false);
  });

  it("openNewWithData 在缺 display_name 时 fallback 到 name 再到 New Agent", () => {
    useAgentEditStore.getState().openNewWithData({});
    expect(useAgentEditStore.getState().agentName).toBe("New Agent");

    useAgentEditStore.getState().openNewWithData({ name: "only-name" });
    expect(useAgentEditStore.getState().agentName).toBe("only-name");
  });

  it("updateField 在 formData 存在时合并字段，缺失时不动", () => {
    useAgentEditStore.getState().openNewWithData({ name: "x", display_name: "X" });
    useAgentEditStore.getState().updateField("description", "updated");
    expect(useAgentEditStore.getState().formData?.description).toBe("updated");

    // Wipe formData and try again — should be a no-op.
    useAgentEditStore.setState({ formData: null });
    useAgentEditStore.getState().updateField("description", "ignored");
    expect(useAgentEditStore.getState().formData).toBeNull();
  });

  it("setSaving 切换 saving 标志位", () => {
    expect(useAgentEditStore.getState().saving).toBe(false);
    useAgentEditStore.getState().setSaving(true);
    expect(useAgentEditStore.getState().saving).toBe(true);
    useAgentEditStore.getState().setSaving(false);
    expect(useAgentEditStore.getState().saving).toBe(false);
  });

  it("setEditingSkillId 切换被编辑的 skill", () => {
    useAgentEditStore.getState().setEditingSkillId("sk-7");
    expect(useAgentEditStore.getState().editingSkillId).toBe("sk-7");
    useAgentEditStore.getState().setEditingSkillId(null);
    expect(useAgentEditStore.getState().editingSkillId).toBeNull();
  });

  it("clearRestoredNotice 清除 restoredDraft", () => {
    useAgentEditStore.setState({ restoredDraft: { agentId: "a", ts: 1 } });
    useAgentEditStore.getState().clearRestoredNotice();
    expect(useAgentEditStore.getState().restoredDraft).toBeNull();
  });
});

describe("agent-edit-store: hasChanges / getChangedFields", () => {
  function seedClean(extra: Partial<AgentMetadata> = {}) {
    const data = fullMetadata(extra);
    useAgentEditStore.setState({
      agentId: "a-1",
      agentName: "Agent One",
      formData: data,
      originalData: JSON.parse(JSON.stringify(data)),
      pendingSkillFiles: {},
      originalSkillFiles: {},
      loading: false,
    });
  }

  it("formData/originalData 缺失时 hasChanges 返回 false", () => {
    expect(useAgentEditStore.getState().hasChanges()).toBe(false);
  });

  it("无修改时 hasChanges 为 false, getChangedFields 为空", () => {
    seedClean();
    expect(useAgentEditStore.getState().hasChanges()).toBe(false);
    expect(useAgentEditStore.getState().getChangedFields()).toEqual({});
  });

  it("普通字段修改后能在 diff 中看到 old/new", () => {
    seedClean();
    useAgentEditStore.getState().updateField("description", "新的描述");
    expect(useAgentEditStore.getState().hasChanges()).toBe(true);
    const diff = useAgentEditStore.getState().getChangedFields();
    expect(diff.description).toEqual({ old: "demo", new: "新的描述" });
  });

  it("mcp_targets 排序后再比较，元素相同顺序不同视作未改动", () => {
    seedClean({ mcp_targets: ["a", "b"] });
    useAgentEditStore.getState().updateField("mcp_targets", ["b", "a"]);
    const diff = useAgentEditStore.getState().getChangedFields();
    expect(diff.mcp_targets).toBeUndefined();

    useAgentEditStore.getState().updateField("mcp_targets", ["b", "c"]);
    const diff2 = useAgentEditStore.getState().getChangedFields();
    expect(diff2.mcp_targets).toBeDefined();
    expect(diff2.mcp_targets.old).toBe("a, b");
    expect(diff2.mcp_targets.new).toBe("b, c");
  });

  it("suggestions 序列化为换行分隔字符串", () => {
    seedClean({ suggestions: ["one"] });
    useAgentEditStore.getState().updateField("suggestions", ["one", "two"]);
    const diff = useAgentEditStore.getState().getChangedFields();
    expect(diff.suggestions).toEqual({ old: "one", new: "one\ntwo" });
  });

  it("跳过 deployedSkillHashes / tools / 时间戳等不可读字段", () => {
    seedClean();
    useAgentEditStore.getState().updateField("updated_at", "2030-01-01");
    useAgentEditStore.getState().updateField("created_at", "2030-01-01");
    useAgentEditStore.getState().updateField("tools", ["new-tool"]);
    const diff = useAgentEditStore.getState().getChangedFields();
    expect(diff.updated_at).toBeUndefined();
    expect(diff.created_at).toBeUndefined();
    expect(diff.tools).toBeUndefined();
  });

  it("仅 skill 文件改动也能让 hasChanges 返回 true", () => {
    const skill = makeSkill();
    seedClean({ skills: [skill] });
    useAgentEditStore.setState({
      originalSkillFiles: { [skill.id]: { "SKILL.md": "old" } },
      pendingSkillFiles: { [skill.id]: { "SKILL.md": "old" } },
    });
    expect(useAgentEditStore.getState().hasChanges()).toBe(false);

    useAgentEditStore.getState().updatePendingSkillFile(skill.id, "SKILL.md", "new content");
    expect(useAgentEditStore.getState().hasChanges()).toBe(true);

    const diff = useAgentEditStore.getState().getChangedFields();
    // Existing skill — no "+" prefix.
    expect(diff[`${skill.name}/SKILL.md`]).toEqual({ old: "old", new: "new content" });
  });

  it("新增 skill 的文件 diff 携带 + 前缀", () => {
    seedClean();
    const skill = makeSkill({ id: "skill-new", name: "new-skill" });
    useAgentEditStore.getState().addSkill(skill);
    useAgentEditStore.getState().updatePendingSkillFile(skill.id, "SKILL.md", "fresh");
    const diff = useAgentEditStore.getState().getChangedFields();
    expect(diff["+new-skill/SKILL.md"]).toEqual({ old: "", new: "fresh" });
  });
});

describe("agent-edit-store: skill CRUD", () => {
  it("addSkill 把 entry 追加到 skills 列表", () => {
    useAgentEditStore.getState().openNewWithData({ name: "x" });
    useAgentEditStore.getState().addSkill(makeSkill({ id: "a" }));
    useAgentEditStore.getState().addSkill(makeSkill({ id: "b", name: "second" }));
    expect(useAgentEditStore.getState().formData?.skills?.map((s) => s.id)).toEqual(["a", "b"]);
  });

  it("addSkill 在 formData 缺失时无副作用", () => {
    useAgentEditStore.setState({ formData: null });
    useAgentEditStore.getState().addSkill(makeSkill());
    expect(useAgentEditStore.getState().formData).toBeNull();
  });

  it("removeSkill 删除 skill 并清理 pending/original 缓存", () => {
    const skill = makeSkill({ id: "s-rm" });
    useAgentEditStore.setState({
      agentId: "agt-1",
      formData: fullMetadata({ skills: [skill] }),
      originalData: fullMetadata({ skills: [skill] }),
      pendingSkillFiles: { [skill.id]: { "SKILL.md": "x" } },
      originalSkillFiles: { [skill.id]: { "SKILL.md": "x" } },
    });

    useAgentEditStore.getState().removeSkill(skill.id);
    const state = useAgentEditStore.getState();
    expect(state.formData?.skills).toEqual([]);
    expect(state.pendingSkillFiles[skill.id]).toBeUndefined();
    expect(state.originalSkillFiles[skill.id]).toBeUndefined();
    expect(mockDeleteFiles).toHaveBeenCalledWith("agt-1", skill.id);
  });

  it("removeSkill 对 draft- 前缀的 agentId 不调用 S3 删除", () => {
    const skill = makeSkill({ id: "s-rm" });
    useAgentEditStore.setState({
      agentId: "draft-abcd1234",
      formData: fullMetadata({ skills: [skill] }),
      originalData: fullMetadata({ skills: [skill] }),
    });
    useAgentEditStore.getState().removeSkill(skill.id);
    expect(mockDeleteFiles).not.toHaveBeenCalled();
  });

  it("updateSkillEntry 仅修改匹配的 skill", () => {
    const a = makeSkill({ id: "a", description: "old-a" });
    const b = makeSkill({ id: "b", description: "old-b" });
    useAgentEditStore.setState({
      agentId: "agt",
      formData: fullMetadata({ skills: [a, b] }),
      originalData: fullMetadata({ skills: [a, b] }),
    });
    useAgentEditStore.getState().updateSkillEntry("a", { description: "new-a" });
    const skills = useAgentEditStore.getState().formData?.skills || [];
    expect(skills.find((s) => s.id === "a")?.description).toBe("new-a");
    expect(skills.find((s) => s.id === "b")?.description).toBe("old-b");
  });

  it("updateSkillEntry 在 formData 缺失时无副作用", () => {
    useAgentEditStore.setState({ formData: null });
    useAgentEditStore.getState().updateSkillEntry("any", { description: "x" });
    expect(useAgentEditStore.getState().formData).toBeNull();
  });
});

describe("agent-edit-store: pending skill files", () => {
  it("setPendingSkillFiles 首次调用同时填充 originalSkillFiles 作为 baseline", () => {
    useAgentEditStore.getState().setPendingSkillFiles("s1", { "a.txt": "v1" });
    const state = useAgentEditStore.getState();
    expect(state.pendingSkillFiles.s1).toEqual({ "a.txt": "v1" });
    expect(state.originalSkillFiles.s1).toEqual({ "a.txt": "v1" });
  });

  it("setPendingSkillFiles 第二次不会覆盖已有的 original baseline", () => {
    useAgentEditStore.getState().setPendingSkillFiles("s1", { "a.txt": "v1" });
    useAgentEditStore.getState().setPendingSkillFiles("s1", { "a.txt": "v2" });
    const state = useAgentEditStore.getState();
    expect(state.pendingSkillFiles.s1).toEqual({ "a.txt": "v2" });
    expect(state.originalSkillFiles.s1).toEqual({ "a.txt": "v1" });
  });

  it("initSkillFiles 仅在 baseline 缺失时写入", () => {
    useAgentEditStore.getState().initSkillFiles("s2", { "x": "1" });
    expect(useAgentEditStore.getState().originalSkillFiles.s2).toEqual({ "x": "1" });
    useAgentEditStore.getState().initSkillFiles("s2", { "x": "2" });
    expect(useAgentEditStore.getState().originalSkillFiles.s2).toEqual({ "x": "1" });
  });

  it("getPendingSkillFiles 取出当前 pending", () => {
    useAgentEditStore.getState().setPendingSkillFiles("s3", { "f": "c" });
    expect(useAgentEditStore.getState().getPendingSkillFiles("s3")).toEqual({ "f": "c" });
    expect(useAgentEditStore.getState().getPendingSkillFiles("nope")).toBeUndefined();
  });

  it("clearPendingSkillFiles 同时清掉 pending 和 original", () => {
    useAgentEditStore.getState().setPendingSkillFiles("s4", { "f": "c" });
    useAgentEditStore.getState().clearPendingSkillFiles("s4");
    const state = useAgentEditStore.getState();
    expect(state.pendingSkillFiles.s4).toBeUndefined();
    expect(state.originalSkillFiles.s4).toBeUndefined();
  });

  it("updatePendingSkillFile 合并到现有文件 map", () => {
    useAgentEditStore.getState().setPendingSkillFiles("s5", { "a": "1" });
    useAgentEditStore.getState().updatePendingSkillFile("s5", "b", "2");
    expect(useAgentEditStore.getState().pendingSkillFiles.s5).toEqual({ a: "1", b: "2" });
  });

  it("updatePendingSkillFile 对未初始化的 skill 也能写入", () => {
    useAgentEditStore.getState().updatePendingSkillFile("brand-new", "f", "v");
    expect(useAgentEditStore.getState().pendingSkillFiles["brand-new"]).toEqual({ f: "v" });
  });
});

describe("agent-edit-store: markSaved", () => {
  it("把 originalData 同步为当前 formData，并清掉 draft", () => {
    const data = fullMetadata({ description: "old" });
    useAgentEditStore.setState({
      agentId: "agt-x",
      formData: data,
      originalData: JSON.parse(JSON.stringify(data)),
      pendingSkillFiles: { s1: { "f": "x" } },
      originalSkillFiles: {},
    });
    useAgentEditStore.getState().updateField("description", "new");
    expect(useAgentEditStore.getState().hasChanges()).toBe(true);

    useAgentEditStore.getState().markSaved();
    const state = useAgentEditStore.getState();
    expect(state.originalData?.description).toBe("new");
    expect(state.originalSkillFiles).toEqual({ s1: { "f": "x" } });
    expect(state.hasChanges()).toBe(false);
    expect(mockClearDraft).toHaveBeenCalledWith("agent-draft:agt-x");
  });

  it("无 formData 时 markSaved 不抛错也不修改 original", () => {
    useAgentEditStore.setState({ agentId: null, formData: null, originalData: null });
    expect(() => useAgentEditStore.getState().markSaved()).not.toThrow();
  });
});

describe("agent-edit-store: loadAgent", () => {
  it("无 draft 时把服务端 metadata 灌入 formData 与 originalData", async () => {
    mockFetchMeta.mockResolvedValue(fullMetadata({ description: "from-server", skills: [] }));
    mockLoadDraft.mockReturnValue(null);

    await useAgentEditStore.getState().loadAgent("agt-1", "Agent One");
    const state = useAgentEditStore.getState();

    expect(state.agentId).toBe("agt-1");
    expect(state.formData?.description).toBe("from-server");
    expect(state.originalData?.description).toBe("from-server");
    expect(state.loading).toBe(false);
    expect(state.restoredDraft).toBeNull();
  });

  it("metadata 为空时 fallback 到只带 name 的 stub", async () => {
    mockFetchMeta.mockResolvedValue(null);

    await useAgentEditStore.getState().loadAgent("agt-empty", "Display");
    const state = useAgentEditStore.getState();
    expect(state.formData?.name).toBe("Display");
    expect(state.formData?.skills).toEqual([]);
  });

  it("有本地 draft 时 formData 来自 draft, originalData 来自 server fresh", async () => {
    mockFetchMeta.mockResolvedValue(fullMetadata({ description: "server-fresh" }));
    mockLoadDraft.mockReturnValue({
      data: {
        formData: { ...fullMetadata({ description: "user-edit" }) },
        pendingSkillFiles: {},
        originalData: fullMetadata({ description: "stale-snapshot" }),
        originalSkillFiles: {},
      },
      ts: 1234567890,
    });

    await useAgentEditStore.getState().loadAgent("agt-d", "X");
    const state = useAgentEditStore.getState();
    expect(state.formData?.description).toBe("user-edit");
    expect(state.originalData?.description).toBe("server-fresh");
    expect(state.restoredDraft).toEqual({ agentId: "agt-d", ts: 1234567890 });
  });

  it("draft 中含 pending skill 时会拉取每个 skill 的服务端 baseline", async () => {
    mockFetchMeta.mockResolvedValue(fullMetadata());
    mockLoadDraft.mockReturnValue({
      data: {
        formData: fullMetadata(),
        pendingSkillFiles: { s1: { "f": "draft" } },
        originalData: fullMetadata(),
        originalSkillFiles: {},
      },
      ts: 1,
    });
    mockFetchBulk.mockResolvedValue({ "f": "server" });

    await useAgentEditStore.getState().loadAgent("agt-bulk", "X");
    const state = useAgentEditStore.getState();
    expect(mockFetchBulk).toHaveBeenCalledWith("agt-bulk", "s1");
    expect(state.originalSkillFiles.s1).toEqual({ "f": "server" });
    expect(state.pendingSkillFiles.s1).toEqual({ "f": "draft" });
  });

  it("tool_definitions 不全时调用 extractToolsFromDeployment 补齐", async () => {
    mockFetchMeta.mockResolvedValue(
      fullMetadata({
        tool_definitions: "",
        tools: ["alpha"],
      }),
    );
    mockExtract.mockResolvedValue({
      tool_definitions: "@tool\ndef alpha(): pass",
      tool_names: "alpha",
    });

    await useAgentEditStore.getState().loadAgent("agt-tools", "X");
    expect(mockExtract).toHaveBeenCalledWith("agt-tools");
    expect(useAgentEditStore.getState().formData?.tool_definitions).toContain("def alpha()");
  });
});
