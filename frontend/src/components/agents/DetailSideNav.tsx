import { useState } from "react";

export interface NavItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
}

export interface NavGroup {
  type: "group";
  id: string;
  label: string;
  items: NavItem[];
}

export type NavEntry = NavItem | NavGroup;

interface Props {
  items: NavEntry[];
  activeId: string | null;
  scrollRootRef: React.RefObject<HTMLElement | null>;
  /** Memory key (typically agent id) — section selection is persisted per key. */
  memoryKey?: string;
  /** Called with the id immediately when a nav item is clicked, so the
   * parent can suppress scroll-spy for the duration of the smooth scroll. */
  onNavigate?: (id: string) => void;
}

const STORAGE_PREFIX = "agent-studio.detail-section.";
const GROUP_PREFIX = "agent-studio.detail-group.";

function sectionKey(key: string): string {
  return `${STORAGE_PREFIX}${key}`;
}

function groupKey(memoryKey: string, groupId: string): string {
  return `${GROUP_PREFIX}${memoryKey}:${groupId}`;
}


function saveSection(key: string, id: string): void {
  if (typeof window === "undefined" || !key) return;
  try {
    window.sessionStorage.setItem(sectionKey(key), id);
  } catch {
    /* ignored */
  }
}

function readGroupOpen(memoryKey: string | undefined, groupId: string): boolean {
  if (typeof window === "undefined" || !memoryKey) return false;
  try {
    return window.sessionStorage.getItem(groupKey(memoryKey, groupId)) === "1";
  } catch {
    return false;
  }
}

function saveGroupOpen(memoryKey: string | undefined, groupId: string, open: boolean): void {
  if (typeof window === "undefined" || !memoryKey) return;
  try {
    window.sessionStorage.setItem(groupKey(memoryKey, groupId), open ? "1" : "0");
  } catch {
    /* ignored */
  }
}

function isGroup(entry: NavEntry): entry is NavGroup {
  return (entry as NavGroup).type === "group";
}

/**
 * Sticky side-nav for AgentDetailPage. Clicking an item scrolls the
 * scroll-root to the matching `<section id="...">`. The active highlight
 * is driven by `activeId` (computed by useScrollSpy in the parent).
 * Section memory is keyed by `memoryKey` in sessionStorage — intentionally
 * NOT in the URL, so we don't tamper with react-router's hash routing.
 * Groups can optionally be collapsible; their open state persists separately
 * under the `agent-studio.detail-group.<memoryKey>:<groupId>` key.
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

  // On mount, restore the saved *active highlight* for the side-nav but
  // do NOT auto-scroll — users expect the page to start at the top when
  // navigating from the agent list. Deep-link scrolling (e.g.
  // /agents/:id/runs/:runId) is handled by the parent page, not here.

  const renderItem = (item: NavItem) => {
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
  };

  return (
    <nav
      aria-label="Section navigation"
      className="hidden lg:block w-48 flex-shrink-0 pr-4"
      data-testid="detail-side-nav"
    >
      <ul className="sticky top-2 space-y-0.5">
        {items.map((entry) =>
          isGroup(entry) ? (
            <GroupBlock key={entry.id} group={entry} memoryKey={memoryKey} renderItem={renderItem} />
          ) : (
            renderItem(entry)
          ),
        )}
      </ul>
    </nav>
  );
}

function GroupBlock({
  group,
  memoryKey,
  renderItem,
}: {
  group: NavGroup;
  memoryKey: string | undefined;
  renderItem: (item: NavItem) => React.ReactNode;
}) {
  const [open, setOpen] = useState<boolean>(() => readGroupOpen(memoryKey, group.id));

  const toggle = () => {
    setOpen((prev) => {
      const next = !prev;
      saveGroupOpen(memoryKey, group.id, next);
      return next;
    });
  };

  return (
    <li
      data-testid={`nav-group-${group.id}`}
      className="pt-2 mt-2 border-t border-gray-200 dark:border-gray-800"
    >
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        data-testid={`nav-group-toggle-${group.id}`}
        className="w-full flex items-center gap-1 px-2.5 py-1 text-[10px] uppercase tracking-wider text-gray-500 dark:text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
      >
        <span className="inline-block w-2">{open ? "▾" : "▸"}</span>
        <span className="truncate">{group.label}</span>
      </button>
      {open && <ul className="mt-1 space-y-0.5">{group.items.map(renderItem)}</ul>}
    </li>
  );
}
