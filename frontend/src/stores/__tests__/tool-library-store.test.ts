import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/tool-storage", () => ({
  scanAllTools: vi.fn(),
  scanDeletedTools: vi.fn(),
  putToolItem: vi.fn(),
  deleteToolItem: vi.fn(),
  softDeleteToolItem: vi.fn(),
  restoreToolItem: vi.fn(),
}));

vi.mock("../../i18n", () => ({
  default: {
    t: (key: string) => key,
  },
}));

import { useToolLibraryStore } from "../tool-library-store";
import {
  scanAllTools,
  scanDeletedTools,
  putToolItem,
  deleteToolItem,
  softDeleteToolItem,
  restoreToolItem,
  type ToolTemplate,
} from "../../lib/tool-storage";

const mockScanAll = vi.mocked(scanAllTools);
const mockScanDeleted = vi.mocked(scanDeletedTools);
const mockPut = vi.mocked(putToolItem);
const mockDelete = vi.mocked(deleteToolItem);
const mockSoftDelete = vi.mocked(softDeleteToolItem);
const mockRestore = vi.mocked(restoreToolItem);

function makeTool(overrides: Partial<ToolTemplate> = {}): ToolTemplate {
  return {
    id: "t-1",
    name: "Tool One",
    description: "desc",
    category: "custom",
    code: "def t(): pass",
    builtin: false,
    owner: "user",
    visibility: "shared",
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useToolLibraryStore.setState({
    tools: [],
    trashedTools: [],
    loading: false,
    saving: false,
    error: null,
  });
});

describe("tool-library-store", () => {
  it("fetchTools 并行加载活跃和已删除工具，按名称排序", async () => {
    mockScanAll.mockResolvedValue([
      makeTool({ id: "b", name: "Banana" }),
      makeTool({ id: "a", name: "Apple" }),
    ]);
    mockScanDeleted.mockResolvedValue([
      makeTool({ id: "z", name: "Zebra" }),
      makeTool({ id: "m", name: "Mango" }),
    ]);

    await useToolLibraryStore.getState().fetchTools();
    const state = useToolLibraryStore.getState();

    expect(mockScanAll).toHaveBeenCalledOnce();
    expect(mockScanDeleted).toHaveBeenCalledOnce();
    expect(state.tools.map((t) => t.name)).toEqual(["Apple", "Banana"]);
    expect(state.trashedTools.map((t) => t.name)).toEqual(["Mango", "Zebra"]);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("fetchTools 失败时清空列表并写入错误信息", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockScanAll.mockRejectedValue(new Error("scan failed"));
    mockScanDeleted.mockResolvedValue([]);

    await useToolLibraryStore.getState().fetchTools();
    const state = useToolLibraryStore.getState();

    expect(state.tools).toEqual([]);
    expect(state.trashedTools).toEqual([]);
    expect(state.loading).toBe(false);
    expect(state.error).toBe("tools.loadFailed");
    errSpy.mockRestore();
  });

  it("saveTool 调用 putToolItem 后重新拉取列表", async () => {
    const tool = makeTool({ id: "new", name: "New Tool" });
    mockPut.mockResolvedValue(undefined);
    mockScanAll.mockResolvedValue([tool]);
    mockScanDeleted.mockResolvedValue([]);

    await useToolLibraryStore.getState().saveTool(tool);
    const state = useToolLibraryStore.getState();

    expect(mockPut).toHaveBeenCalledWith(tool);
    expect(mockScanAll).toHaveBeenCalledOnce();
    expect(state.tools).toHaveLength(1);
    expect(state.saving).toBe(false);
    expect(state.error).toBeNull();
  });

  it("saveTool 失败时设置 error 并向上抛出", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockPut.mockRejectedValue(new Error("put failed"));
    const tool = makeTool();

    await expect(useToolLibraryStore.getState().saveTool(tool)).rejects.toThrow("put failed");
    const state = useToolLibraryStore.getState();
    expect(state.saving).toBe(false);
    expect(state.error).toBe("tools.saveFailed");
    errSpy.mockRestore();
  });

  it("deleteTool 永久删除并刷新列表", async () => {
    mockDelete.mockResolvedValue(undefined);
    mockScanAll.mockResolvedValue([]);
    mockScanDeleted.mockResolvedValue([]);

    await useToolLibraryStore.getState().deleteTool("t-1");

    expect(mockDelete).toHaveBeenCalledWith("t-1");
    expect(mockScanAll).toHaveBeenCalledOnce();
    expect(useToolLibraryStore.getState().saving).toBe(false);
  });

  it("deleteTool 失败时设置 error 并抛出", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockDelete.mockRejectedValue(new Error("nope"));

    await expect(useToolLibraryStore.getState().deleteTool("t-1")).rejects.toThrow("nope");
    expect(useToolLibraryStore.getState().error).toBe("tools.deleteFailed");
    errSpy.mockRestore();
  });

  it("softDeleteTool 软删后从 active 移到 trashed", async () => {
    const tool = makeTool({ id: "t-1", name: "Trashed" });
    mockSoftDelete.mockResolvedValue(undefined);
    mockScanAll.mockResolvedValue([]);
    mockScanDeleted.mockResolvedValue([tool]);

    await useToolLibraryStore.getState().softDeleteTool("t-1");
    const state = useToolLibraryStore.getState();

    expect(mockSoftDelete).toHaveBeenCalledWith("t-1");
    expect(state.tools).toEqual([]);
    expect(state.trashedTools).toHaveLength(1);
    expect(state.saving).toBe(false);
  });

  it("softDeleteTool 失败时设置 error 并抛出", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockSoftDelete.mockRejectedValue(new Error("soft fail"));

    await expect(useToolLibraryStore.getState().softDeleteTool("t-1")).rejects.toThrow("soft fail");
    expect(useToolLibraryStore.getState().error).toBe("tools.deleteFailed");
    errSpy.mockRestore();
  });

  it("restoreTool 把工具从 trashed 还原到 active", async () => {
    const tool = makeTool({ id: "t-1", name: "Back" });
    mockRestore.mockResolvedValue(undefined);
    mockScanAll.mockResolvedValue([tool]);
    mockScanDeleted.mockResolvedValue([]);

    await useToolLibraryStore.getState().restoreTool("t-1");
    const state = useToolLibraryStore.getState();

    expect(mockRestore).toHaveBeenCalledWith("t-1");
    expect(state.tools).toHaveLength(1);
    expect(state.trashedTools).toEqual([]);
    expect(state.saving).toBe(false);
  });

  it("restoreTool 失败时设置 error 并抛出", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockRestore.mockRejectedValue(new Error("restore fail"));

    await expect(useToolLibraryStore.getState().restoreTool("t-1")).rejects.toThrow("restore fail");
    expect(useToolLibraryStore.getState().error).toBe("tools.restoreFailed");
    errSpy.mockRestore();
  });

  it("clearError 把 error 重置为 null", () => {
    useToolLibraryStore.setState({
      tools: [],
      trashedTools: [],
      loading: false,
      saving: false,
      error: "boom",
    });

    useToolLibraryStore.getState().clearError();
    expect(useToolLibraryStore.getState().error).toBeNull();
  });
});
