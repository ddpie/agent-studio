import { create } from "zustand";
import { apiGet, apiPost, apiPut, apiDelete } from "../lib/api-client";

export interface RoutingRule {
  type: string;
  chatId?: string;
  prefix?: string;
  agentId: string;
}

export interface Channel {
  channelId: string;
  channelType: "feishu" | "dingtalk" | "slack";
  channelName: string;
  status: "active" | "paused" | "error" | "provisioning";
  defaultAgentId: string;
  routingRules: RoutingRule[];
  triggerMode: "mention" | "all" | "keyword";
  platformConfig: Record<string, unknown>;
  lastMessageAt?: string;
  messageCount?: number;
  lastError?: string;
  createdAt: string;
}

interface ChannelStore {
  channels: Channel[];
  loading: boolean;
  error: string | null;
  fetchChannels: () => Promise<void>;
  createChannel: (data: Partial<Channel> & { appSecret: string }) => Promise<Channel>;
  updateChannel: (channelId: string, data: Partial<Channel>) => Promise<void>;
  deleteChannel: (channelId: string) => Promise<void>;
}

export const useChannelStore = create<ChannelStore>((set) => ({
  channels: [],
  loading: false,
  error: null,

  fetchChannels: async () => {
    set({ loading: true, error: null });
    try {
      const resp = await apiGet<{ channels: Channel[] }>("/channels");
      set({ channels: resp.channels || [], loading: false });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to fetch channels";
      set({ error: msg, loading: false });
    }
  },

  createChannel: async (data) => {
    const resp = await apiPost<Channel>("/channels", data);
    set((state) => ({ channels: [...state.channels, resp] }));
    return resp;
  },

  updateChannel: async (channelId, data) => {
    await apiPut(`/channels/${encodeURIComponent(channelId)}`, data);
    set((state) => ({
      channels: state.channels.map((ch) =>
        ch.channelId === channelId ? { ...ch, ...data } : ch
      ),
    }));
  },

  deleteChannel: async (channelId) => {
    await apiDelete(`/channels/${encodeURIComponent(channelId)}`);
    set((state) => ({
      channels: state.channels.filter((ch) => ch.channelId !== channelId),
    }));
  },
}));
