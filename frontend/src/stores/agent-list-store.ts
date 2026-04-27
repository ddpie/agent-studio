import { create } from "zustand";
import { fetchAgents, type AgentListItem } from "../lib/api-client";

export interface AgentInfo {
  name: string;
  displayName: string;
  id: string;
  status: string;
  description: string;
  runtime_type?: "zip" | "harness";
}

interface AgentListState {
  agents: AgentInfo[];
  archivedAgents: AgentInfo[];
  loading: boolean;
  fetchAgents: () => Promise<void>;
}

const MAX_PAGES = 20;

export const useAgentListStore = create<AgentListState>((set) => ({
  agents: [],
  archivedAgents: [],
  loading: false,

  fetchAgents: async () => {
    set({ loading: true });
    try {
      const allItems: AgentListItem[] = [];
      let cursor: string | undefined;
      let pages = 0;
      do {
        const resp = await fetchAgents(cursor);
        allItems.push(...resp.items);
        cursor = resp.nextCursor;
        pages++;
        if (pages >= MAX_PAGES) {
          console.warn("Agent list pagination hit safety limit");
          break;
        }
      } while (cursor);

      const agents: AgentInfo[] = [];
      const archivedAgents: AgentInfo[] = [];

      for (const item of allItems) {
        const info: AgentInfo = {
          name: item.name || item.agentId,
          displayName: item.display_name || item.name || item.agentId,
          id: item.agentId,
          status: item.status || "active",
          description: item.description || "",
          runtime_type: item.runtime_type,
        };
        if (item.status === "archived") {
          archivedAgents.push(info);
        } else {
          agents.push(info);
        }
      }

      set({ agents, archivedAgents, loading: false });
    } catch (err) {
      console.error("Failed to fetch agents:", err);
      set({ agents: [], archivedAgents: [], loading: false });
    }
  },
}));
