import { create } from "zustand";

type NavSection = "agents" | "skills" | "mcp" | "settings";

interface NavState {
  activeSection: NavSection;
  setSection: (section: NavSection) => void;
}

export const useNavStore = create<NavState>((set) => ({
  activeSection: "agents",
  setSection: (section) => set({ activeSection: section }),
}));
