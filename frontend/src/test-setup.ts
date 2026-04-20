import "@testing-library/jest-dom/vitest";

// JSDOM doesn't implement IntersectionObserver. Components that use it
// (useScrollSpy, LazySection) would throw at render time during unit
// tests. A no-op polyfill keeps the render path clean; LazySection also
// short-circuits to eager in test mode so children actually mount.
if (typeof globalThis.IntersectionObserver === "undefined") {
  class NoopIntersectionObserver {
    root = null;
    rootMargin = "";
    thresholds: readonly number[] = [];
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  (globalThis as unknown as { IntersectionObserver: typeof IntersectionObserver }).IntersectionObserver =
    NoopIntersectionObserver as unknown as typeof IntersectionObserver;
}
