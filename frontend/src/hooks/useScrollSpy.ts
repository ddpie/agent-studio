import { useEffect, useRef, useState } from "react";

/**
 * Returns the id of the section currently most in view, plus a helper
 * `suppressFor(ms)` that temporarily ignores scroll-spy updates (used to
 * pin the active id while a programmatic smooth-scroll is in flight —
 * otherwise the highlight strobes through intermediate sections).
 *
 * Uses IntersectionObserver with a vertical rootMargin so the "active"
 * section is whatever sits in the top ~30% of the viewport — matches
 * typical docs-site sidebar behaviour. `rootRef` should be the scroll
 * container (the overflow:auto parent); pass a null-ref to observe the
 * page.
 */
export function useScrollSpy(
  sectionIds: string[],
  rootRef: React.RefObject<HTMLElement | null>,
): { activeId: string | null; suppressFor: (ms: number) => void; setActiveId: (id: string) => void } {
  const [active, setActive] = useState<string | null>(sectionIds[0] ?? null);
  const lastActiveRef = useRef<string | null>(active);
  const suppressUntilRef = useRef<number>(0);
  const forcedActiveRef = useRef<string | null>(null);

  useEffect(() => {
    const rootEl = rootRef.current ?? null;
    // Track ratio per section so the tie-break picks the one most in
    // view rather than the DOM-earliest one. Map keyed by id.
    const ratios = new Map<string, number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          const id = (e.target as HTMLElement).id;
          if (e.isIntersecting) {
            ratios.set(id, e.intersectionRatio);
          } else {
            ratios.delete(id);
          }
        }

        // Suppress updates while a programmatic scroll is in progress.
        if (Date.now() < suppressUntilRef.current) {
          if (forcedActiveRef.current && forcedActiveRef.current !== lastActiveRef.current) {
            lastActiveRef.current = forcedActiveRef.current;
            setActive(forcedActiveRef.current);
          }
          return;
        }

        let next: string | null = null;
        let bestRatio = -1;
        for (const id of sectionIds) {
          const r = ratios.get(id);
          if (r == null) continue;
          if (r > bestRatio) {
            bestRatio = r;
            next = id;
          }
        }
        // `next === null` means no section is within the focus band — keep
        // the last active (avoids flicker when scrolling through a gap).
        if (next && next !== lastActiveRef.current) {
          lastActiveRef.current = next;
          setActive(next);
        }
      },
      {
        root: rootEl,
        rootMargin: "-20% 0px -70% 0px",
        threshold: 0,
      },
    );

    for (const id of sectionIds) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionIds.join(",")]);

  const suppressFor = (ms: number) => {
    suppressUntilRef.current = Date.now() + ms;
  };

  const setActiveId = (id: string) => {
    forcedActiveRef.current = id;
    lastActiveRef.current = id;
    setActive(id);
  };

  return { activeId: active, suppressFor, setActiveId };
}

/**
 * Trigger a callback the first time an element enters the viewport.
 * Lightweight wrapper around IntersectionObserver with rootMargin padding
 * so the child renders slightly before it's actually visible.
 */
export function useOnFirstVisible(
  elRef: React.RefObject<HTMLElement | null>,
  onVisible: () => void,
  rootRef?: React.RefObject<HTMLElement | null>,
  rootMargin = "200px",
): void {
  const firedRef = useRef(false);
  useEffect(() => {
    const el = elRef.current;
    if (!el || firedRef.current) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting && !firedRef.current) {
            firedRef.current = true;
            onVisible();
            io.disconnect();
            break;
          }
        }
      },
      { root: rootRef?.current ?? null, rootMargin },
    );
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
