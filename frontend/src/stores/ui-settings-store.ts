import { create } from "zustand";
import { persist } from "zustand/middleware";

type Theme = "light" | "dark" | "system";
type Language = "zh" | "en";

interface UISettingsState {
  sidebarWidth: number;
  inputHeight: number;
  theme: Theme;
  language: Language;
  // When true (default), assistant tool calls render inline with the
  // prose so readers see the sequence: "said A, called X, said B".
  // When false, all tool calls stack at the end of the message (the
  // pre-blocks render path). Persisted so users keep their pick.
  showInlineToolCalls: boolean;
  setSidebarWidth: (width: number) => void;
  setInputHeight: (height: number) => void;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: Language) => void;
  setShowInlineToolCalls: (show: boolean) => void;
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
      showInlineToolCalls: true,
      setSidebarWidth: (width) => set({ sidebarWidth: width }),
      setInputHeight: (height) => set({ inputHeight: height }),
      setTheme: (theme) => {
        applyTheme(theme);
        set({ theme });
      },
      setLanguage: (language) => set({ language }),
      setShowInlineToolCalls: (show) => set({ showInlineToolCalls: show }),
    }),
    {
      name: "agent-studio-ui",
      onRehydrateStorage: () => (state) => {
        if (state?.theme) applyTheme(state.theme);
      },
    }
  )
);
