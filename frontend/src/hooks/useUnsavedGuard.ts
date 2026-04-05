/**
 * useUnsavedGuard — unified hook for unsaved changes protection.
 * Combines: beforeunload warning, react-router useBlocker, Ctrl+S shortcut.
 */
import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

interface UnsavedGuardOptions {
  hasChanges: boolean;
  onSave?: () => void;
  saving?: boolean;
}

export default function useUnsavedGuard({ hasChanges, onSave, saving }: UnsavedGuardOptions) {
  // beforeunload warning
  useEffect(() => {
    if (!hasChanges) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasChanges]);

  // Ctrl+S / Cmd+S shortcut
  const saveRef = useRef(onSave);
  useEffect(() => { saveRef.current = onSave; });
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        if (hasChanges && !saving) saveRef.current?.();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [hasChanges, saving]);

  // Route navigation blocker (only blocks pathname changes, not search params)
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (!hasChanges) return false;
    return currentLocation.pathname !== nextLocation.pathname;
  });

  return blocker;
}
