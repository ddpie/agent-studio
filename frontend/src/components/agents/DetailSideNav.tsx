import { useEffect } from "react";

export interface NavItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

interface Props {
  items: NavItem[];
  activeId: string | null;
  scrollRootRef: React.RefObject<HTMLElement | null>;
  /** Memory key (typically agent id) — section selection is persisted per key. */
  memoryKey?: string;
  /** Called with the id immediately when a nav item is clicked, so the
   * parent can suppress scroll-spy for the duration of the smooth scroll. */
  onNavigate?: (id: string) => void;
}

const STORAGE_PREFIX = "agent-studio.detail-section.";

function storageKey(key: string): string {
  return `${STORAGE_PREFIX}${key}`;
}

/** Read the section id last saved for this memory key, or null. */
export function readSavedSection(key: string): string | null {
  if (typeof window === "undefined" || !key) return null;
  try {
    return window.sessionStorage.getItem(storageKey(key));
  } catch {
    return null;
  }
}

function saveSection(key: string, id: string): void {
  if (typeof window === "undefined" || !key) return;
  try {
    window.sessionStorage.setItem(storageKey(key), id);
  } catch {
    /* ignored */
  }
}

/**
 * Sticky side-nav for AgentDetailPage. Clicking an item scrolls the
 * scroll-root to the matching `<section id="...">`. The active highlight
 * is driven by `activeId` (computed by useScrollSpy in the parent).
 * Section memory is keyed by `memoryKey` in sessionStorage — intentionally
 * NOT in the URL, so we don't tamper with react-router's hash routing.
 */
export default function DetailSideNav({
  items,
  activeId,
  scrollRootRef,
  memoryKey,
  onNavigate,
}: Props) {
  const scrollToSection = (id: string) => {
    const el = document.getElementById(id);
    if (!el) return;
    const root = scrollRootRef.current;
    if (root) {
      const topWithin =
        el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 16;
      root.scrollTo({ top: topWithin, behavior: "smooth" });
    } else {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    // Move focus to the section container for a11y (screen readers
    // announce the heading on focus). preventScroll avoids a second jump.
    el.focus({ preventScroll: true });
  };

  const handleClick = (id: string) => {
    onNavigate?.(id);
    if (memoryKey) saveSection(memoryKey, id);
    scrollToSection(id);
  };

  // Restore saved section on mount (or when the memory key changes).
  useEffect(() => {
    if (!memoryKey) return;
    const saved = readSavedSection(memoryKey);
    if (!saved) return;
    const el = document.getElementById(saved);
    if (!el) return;
    requestAnimationFrame(() => {
      const root = scrollRootRef.current;
      if (!root) return;
      const topWithin =
        el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 16;
      root.scrollTo({ top: topWithin, behavior: "auto" });
    });
  }, [memoryKey, scrollRootRef]);

  return (
    <nav
      aria-label="Section navigation"
      className="hidden lg:block w-48 flex-shrink-0 pr-4"
      data-testid="detail-side-nav"
    >
      <ul className="sticky top-2 space-y-0.5">
        {items.map((item) => {
          const isActive = activeId === item.id;
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => handleClick(item.id)}
                data-testid={`nav-${item.id}`}
                aria-controls={item.id}
                aria-current={isActive ? "location" : undefined}
                className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs transition-colors text-left ${
                  isActive
                    ? "bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 font-medium"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800/60"
                }`}
              >
                {item.icon && <span className="flex-shrink-0 w-3.5 h-3.5">{item.icon}</span>}
                <span className="truncate">{item.label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
