import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../lib/api-client", () => ({
  fetchKnowledgeBases: vi.fn(),
  fetchKnowledgeBase: vi.fn(),
  createKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  uploadKBDocument: vi.fn(),
  deleteKBDocument: vi.fn(),
  fetchKBIngestion: vi.fn(),
}));

import { useKBStore } from "../kb-store";
import {
  fetchKnowledgeBases,
  fetchKnowledgeBase,
  createKnowledgeBase,
  deleteKnowledgeBase,
  uploadKBDocument,
  deleteKBDocument,
  fetchKBIngestion,
} from "../../lib/api-client";

const mockList = vi.mocked(fetchKnowledgeBases);
const mockDetail = vi.mocked(fetchKnowledgeBase);
const mockCreate = vi.mocked(createKnowledgeBase);
const mockDelete = vi.mocked(deleteKnowledgeBase);
const mockUpload = vi.mocked(uploadKBDocument);
const mockDeleteDoc = vi.mocked(deleteKBDocument);
const mockIngestion = vi.mocked(fetchKBIngestion);

const initialState = {
  items: [],
  loading: false,
  error: null,
  currentDetail: null,
  detailLoading: false,
  detailError: null,
};

const sampleListItem = {
  kbId: "kb-1",
  name: "KB One",
  description: "first",
  status: "READY",
  docCount: 3,
  updatedAt: "2026-06-01",
};

const sampleDetail = {
  ...sampleListItem,
  bedrockKbId: "bedrock-1",
  embeddingModel: "titan-v2",
  documents: [],
  ingestion: null,
  attachedAgentIds: [],
  createdAt: "2026-05-01",
  createdBy: "user-1",
};

beforeEach(() => {
  vi.clearAllMocks();
  useKBStore.setState(initialState);
});

describe("kb-store", () => {
  describe("fetchList", () => {
    it("populates items on success", async () => {
      mockList.mockResolvedValue({ items: [sampleListItem] });
      await useKBStore.getState().fetchList();
      const s = useKBStore.getState();
      expect(s.items).toHaveLength(1);
      expect(s.loading).toBe(false);
      expect(s.error).toBeNull();
    });

    it("captures error message on failure", async () => {
      mockList.mockRejectedValue(new Error("network down"));
      await useKBStore.getState().fetchList();
      const s = useKBStore.getState();
      expect(s.items).toEqual([]);
      expect(s.loading).toBe(false);
      expect(s.error).toBe("network down");
    });

    it("falls back to a default error message when none is provided", async () => {
      mockList.mockRejectedValue({});
      await useKBStore.getState().fetchList();
      expect(useKBStore.getState().error).toBe("Failed to fetch knowledge bases");
    });
  });

  describe("fetchDetail", () => {
    it("populates currentDetail on success", async () => {
      mockDetail.mockResolvedValue(sampleDetail);
      await useKBStore.getState().fetchDetail("kb-1");
      const s = useKBStore.getState();
      expect(s.currentDetail?.kbId).toBe("kb-1");
      expect(s.detailLoading).toBe(false);
      expect(s.detailError).toBeNull();
      expect(mockDetail).toHaveBeenCalledWith("kb-1");
    });

    it("clears currentDetail and surfaces error on failure", async () => {
      useKBStore.setState({ currentDetail: sampleDetail });
      mockDetail.mockRejectedValue(new Error("404"));
      await useKBStore.getState().fetchDetail("kb-1");
      const s = useKBStore.getState();
      expect(s.currentDetail).toBeNull();
      expect(s.detailError).toBe("404");
    });

    it("falls back to a default error message", async () => {
      mockDetail.mockRejectedValue({});
      await useKBStore.getState().fetchDetail("kb-1");
      expect(useKBStore.getState().detailError).toBe("Failed to fetch knowledge base");
    });
  });

  describe("create", () => {
    it("creates and refreshes the list", async () => {
      mockCreate.mockResolvedValue({ kbId: "kb-2", bedrockKbId: "br-2", status: "CREATING" });
      mockList.mockResolvedValue({ items: [sampleListItem, { ...sampleListItem, kbId: "kb-2", name: "Two" }] });

      const resp = await useKBStore.getState().create("Two", "desc");
      expect(resp.kbId).toBe("kb-2");
      expect(mockCreate).toHaveBeenCalledWith("Two", "desc");
      expect(mockList).toHaveBeenCalled();
      expect(useKBStore.getState().items).toHaveLength(2);
    });
  });

  describe("deleteKB", () => {
    it("deletes, clears currentDetail, and refreshes list", async () => {
      useKBStore.setState({ currentDetail: sampleDetail });
      mockDelete.mockResolvedValue({ deleted: true });
      mockList.mockResolvedValue({ items: [] });

      await useKBStore.getState().deleteKB("kb-1");
      const s = useKBStore.getState();
      expect(mockDelete).toHaveBeenCalledWith("kb-1");
      expect(s.currentDetail).toBeNull();
      expect(s.items).toEqual([]);
    });
  });

  describe("uploadDocument", () => {
    it("uploads then re-fetches detail", async () => {
      mockUpload.mockResolvedValue({ documentKey: "doc-1", ingestionJobId: "j1", status: "STARTED" });
      mockDetail.mockResolvedValue(sampleDetail);

      await useKBStore.getState().uploadDocument("kb-1", "stage/x", "file.pdf");
      expect(mockUpload).toHaveBeenCalledWith("kb-1", "stage/x", "file.pdf");
      expect(mockDetail).toHaveBeenCalledWith("kb-1");
    });
  });

  describe("deleteDocument", () => {
    it("deletes a doc then re-fetches detail", async () => {
      mockDeleteDoc.mockResolvedValue({ deleted: true, ingestionJobId: "j2" });
      mockDetail.mockResolvedValue(sampleDetail);

      await useKBStore.getState().deleteDocument("kb-1", "doc-1");
      expect(mockDeleteDoc).toHaveBeenCalledWith("kb-1", "doc-1");
      expect(mockDetail).toHaveBeenCalledWith("kb-1");
    });
  });

  describe("checkIngestion", () => {
    it("merges ingestion status into matching currentDetail", async () => {
      useKBStore.setState({ currentDetail: sampleDetail });
      const ingestion = {
        status: "INDEXING",
        documentsScanned: 5,
        documentsIndexed: 4,
        documentsFailed: 1,
        failureReasons: ["bad-pdf"],
      };
      mockIngestion.mockResolvedValue(ingestion);

      const resp = await useKBStore.getState().checkIngestion("kb-1");
      expect(resp).toEqual(ingestion);
      const merged = useKBStore.getState().currentDetail!;
      expect(merged.ingestion?.status).toBe("INDEXING");
      expect(merged.ingestion?.documentsIndexed).toBe(4);
      // The store hard-codes jobId="" when merging — keep that behaviour pinned.
      expect(merged.ingestion?.jobId).toBe("");
    });

    it("does not mutate currentDetail when kbId mismatches", async () => {
      useKBStore.setState({ currentDetail: sampleDetail });
      mockIngestion.mockResolvedValue({
        status: "READY",
        documentsScanned: 0,
        documentsIndexed: 0,
        documentsFailed: 0,
        failureReasons: [],
      });

      await useKBStore.getState().checkIngestion("other-kb");
      expect(useKBStore.getState().currentDetail).toEqual(sampleDetail);
    });

    it("returns the response even when no current detail exists", async () => {
      const ingestion = {
        status: "READY",
        documentsScanned: 1,
        documentsIndexed: 1,
        documentsFailed: 0,
        failureReasons: [],
      };
      mockIngestion.mockResolvedValue(ingestion);
      const resp = await useKBStore.getState().checkIngestion("kb-1");
      expect(resp).toEqual(ingestion);
      expect(useKBStore.getState().currentDetail).toBeNull();
    });
  });
});
