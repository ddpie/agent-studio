import { useRef, useState, useEffect } from "react";
import { useOnFirstVisible } from "../../hooks/useScrollSpy";

interface Props {
  id: string;
  testId?: string;
  rootRef?: React.RefObject<HTMLElement | null>;
  /** Force-render even if not yet visible (used for the initially-active section). */
  eager?: boolean;
  /** Estimated height so scroll-spy and anchor-scroll work before content loads. */
  minHeight?: number;
  children: React.ReactNode;
  className?: string;
}

// In test / SSR environments IntersectionObserver is polyfilled as a
// no-op, which means children would never mount. Short-circuit to eager
// so JSDOM-based tests can assert on real child content. Vitest sets
// `import.meta.env.MODE = "test"` so we check that without pulling in
// node types.
const TEST_ENV =
  typeof import.meta !== "undefined" &&
  (import.meta as ImportMeta & { env?: { MODE?: string } }).env?.MODE === "test";

/**
 * Renders children only once the section scrolls near the viewport.
 * Keeps the placeholder at a sensible min-height so the page scroll
 * position and the sticky-nav scroll-spy remain stable as sections
 * hydrate lazily.
 */
export default function LazySection({
  id,
  testId,
  rootRef,
  eager = false,
  minHeight = 200,
  children,
  className,
}: Props) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(eager || TEST_ENV);

  useOnFirstVisible(ref, () => setVisible(true), rootRef);

  useEffect(() => {
    if (eager && !visible) setVisible(true);
  }, [eager, visible]);

  return (
    <section
      id={id}
      ref={ref}
      data-testid={testId}
      // `tabIndex={-1}` lets the side-nav programmatically move focus
      // here after a jump, so screen readers announce the heading.
      tabIndex={-1}
      className={`focus:outline-none ${className ?? ""}`}
      style={{ scrollMarginTop: 16, minHeight: visible ? undefined : minHeight }}
    >
      {visible ? children : null}
    </section>
  );
}
