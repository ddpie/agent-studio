import { create } from "zustand";
import {
  fetchKnowledgeBases,
  fetchKnowledgeBase,
  createKnowledgeBase,
  deleteKnowledgeBase,
  uploadKBDocument,
  deleteKBDocument,
  fetchKBIngestion,
  type KBListItem,
  type KBDetailResponse,
  type KBIngestionResponse,
} from "../lib/api-client";

interface KBState {
  items: KBListItem[];
  loading: boolean;
  error: string | null;
  currentDetail: KBDetailResponse | null;
  detailLoading: boolean;
  detailError: string | null;

  fetchList: () => Promise<void>;
  fetchDetail: (kbId: string) => Promise<void>;
  create: (name: string, description: string) => Promise<{ kbId: string; bedrockKbId: string; status: string }>;
  deleteKB: (kbId: string) => Promise<void>;
  uploadDocument: (kbId: string, stagingKey: string, filename: string) => Promise<void>;
  deleteDocument: (kbId: string, documentKey: string) => Promise<void>;
  checkIngestion: (kbId: string) => Promise<KBIngestionResponse>;
}

export const useKBStore = create<KBState>((set, get) => ({
  items: [],
  loading: false,
  error: null,
  currentDetail: null,
  detailLoading: false,
  detailError: null,

  fetchList: async () => {
    set({ loading: true, error: null });
    try {
      const resp = await fetchKnowledgeBases();
      set({ items: resp.items, loading: false });
    } catch (e: any) {
      set({ error: e.message || "Failed to fetch knowledge bases", loading: false });
    }
  },

  fetchDetail: async (kbId: string) => {
    set({ detailLoading: true, detailError: null });
    try {
      const detail = await fetchKnowledgeBase(kbId);
      set({ currentDetail: detail, detailLoading: false });
    } catch (e: any) {
      set({ detailError: e.message || "Failed to fetch knowledge base", detailLoading: false, currentDetail: null });
    }
  },

  create: async (name: string, description: string) => {
    const resp = await createKnowledgeBase(name, description);
    await get().fetchList();
    return resp;
  },

  deleteKB: async (kbId: string) => {
    await deleteKnowledgeBase(kbId);
    set({ currentDetail: null });
    await get().fetchList();
  },

  uploadDocument: async (kbId: string, stagingKey: string, filename: string) => {
    await uploadKBDocument(kbId, stagingKey, filename);
    await get().fetchDetail(kbId);
  },

  deleteDocument: async (kbId: string, documentKey: string) => {
    await deleteKBDocument(kbId, documentKey);
    await get().fetchDetail(kbId);
  },

  checkIngestion: async (kbId: string) => {
    const resp = await fetchKBIngestion(kbId);
    const detail = get().currentDetail;
    if (detail && detail.kbId === kbId) {
      set({ currentDetail: { ...detail, ingestion: { ...resp, jobId: "" } } });
    }
    return resp;
  },
}));
