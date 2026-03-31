import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UISettingsState {
  sidebarWidth: number;
  inputHeight: number;
  setSidebarWidth: (width: number) => void;
  setInputHeight: (height: number) => void;
}

export const useUISettings = create<UISettingsState>()(
  persist(
    (set) => ({
      sidebarWidth: 224,
      inputHeight: 44,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      setInputHeight: (height) => set({ inputHeight: height }),
    }),
    { name: "agent-studio-ui" }
  )
);
