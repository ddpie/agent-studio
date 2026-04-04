import { create } from "zustand";
import { persist } from "zustand/middleware";

type Theme = "light" | "dark" | "system";
type Language = "zh" | "en";

interface UISettingsState {
  sidebarWidth: number;
  inputHeight: number;
  theme: Theme;
  language: Language;
  setSidebarWidth: (width: number) => void;
  setInputHeight: (height: number) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme === "system") {
    const isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    root.classList.toggle("dark", isDark);
  } else {
    root.classList.toggle("dark", theme === "dark");
  }
}

export const useUISettings = create<UISettingsState>()(
  persist(
    (set) => ({
      sidebarWidth: 224,
      inputHeight: 44,
      theme: "light" as Theme,
      language: "zh" as Language,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      setInputHeight: (height) => set({ inputHeight: height }),
      setTheme: (theme) => {
        applyTheme(theme);
        set({ theme });
      },
      setLanguage: (language) => set({ language }),
    }),
    {
      name: "agent-studio-ui",
      onRehydrateStorage: () => (state) => {
        if (state?.theme) applyTheme(state.theme);
      },
    }
  )
);
