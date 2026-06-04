import { describe, it, expect, beforeEach, vi } from "vitest";
import { useUISettings } from "../ui-settings-store";

// matchMedia is not implemented in jsdom; the "system" theme branch reads it.
beforeEach(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  }
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  // Reset to the store's documented defaults — persist middleware would
  // otherwise leak state across tests.
  useUISettings.setState({
    sidebarWidth: 224,
    inputHeight: 44,
    theme: "light",
    language: "zh",
    showInlineToolCalls: true,
  });
});

describe("ui-settings-store", () => {
  it("exposes the documented defaults", () => {
    const s = useUISettings.getState();
    expect(s.sidebarWidth).toBe(224);
    expect(s.inputHeight).toBe(44);
    expect(s.theme).toBe("light");
    expect(s.language).toBe("zh");
    expect(s.showInlineToolCalls).toBe(true);
  });

  it("setSidebarWidth updates state", () => {
    useUISettings.getState().setSidebarWidth(320);
    expect(useUISettings.getState().sidebarWidth).toBe(320);
  });

  it("setInputHeight updates state", () => {
    useUISettings.getState().setInputHeight(80);
    expect(useUISettings.getState().inputHeight).toBe(80);
  });

  it("setTheme('dark') toggles .dark on documentElement", () => {
    useUISettings.getState().setTheme("dark");
    expect(useUISettings.getState().theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setTheme('light') removes .dark on documentElement", () => {
    document.documentElement.classList.add("dark");
    useUISettings.getState().setTheme("light");
    expect(useUISettings.getState().theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("setTheme('system') uses prefers-color-scheme media query", () => {
    (window.matchMedia as ReturnType<typeof vi.fn>).mockReturnValueOnce({
      matches: true,
      media: "(prefers-color-scheme: dark)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    });
    useUISettings.getState().setTheme("system");
    expect(useUISettings.getState().theme).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setLanguage updates state without touching DOM", () => {
    useUISettings.getState().setLanguage("en");
    expect(useUISettings.getState().language).toBe("en");
  });

  it("setShowInlineToolCalls flips the flag", () => {
    useUISettings.getState().setShowInlineToolCalls(false);
    expect(useUISettings.getState().showInlineToolCalls).toBe(false);
    useUISettings.getState().setShowInlineToolCalls(true);
    expect(useUISettings.getState().showInlineToolCalls).toBe(true);
  });

  it("persist middleware writes to localStorage under 'agent-studio-ui'", () => {
    useUISettings.getState().setSidebarWidth(300);
    useUISettings.getState().setLanguage("en");
    const raw = localStorage.getItem("agent-studio-ui");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    // zustand persist envelope: { state: {...}, version: number }
    expect(parsed.state.sidebarWidth).toBe(300);
    expect(parsed.state.language).toBe("en");
  });
});
