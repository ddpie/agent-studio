/**
 * LazyMonaco — Lazy-loaded wrappers for Monaco Editor and DiffEditor.
 * These use React.lazy + Suspense to code-split the ~1.5MB Monaco bundle
 * out of the initial load, since editors are only needed in detail views.
 */
import { lazy, Suspense, forwardRef, type ComponentProps } from "react";

// Lazy-load the default export (Editor)
const MonacoEditorLazy = lazy(() => import("@monaco-editor/react"));

// Lazy-load the named export (DiffEditor) — need to re-export as default
const DiffEditorLazy = lazy(() =>
  import("@monaco-editor/react").then((mod) => ({ default: mod.DiffEditor }))
);

function EditorSkeleton() {
  return (
    <div className="w-full h-full min-h-[200px] flex items-center justify-center bg-gray-50 dark:bg-gray-900 animate-pulse">
      <div className="text-xs text-gray-400 dark:text-gray-600">Loading editor...</div>
    </div>
  );
}

type EditorProps = ComponentProps<typeof MonacoEditorLazy>;
type DiffEditorProps = ComponentProps<typeof DiffEditorLazy>;

/**
 * Drop-in replacement for `import Editor from "@monaco-editor/react"`.
 * Wraps in Suspense so the 1.5MB Monaco bundle loads on demand.
 */
export const LazyMonacoEditor = forwardRef<unknown, EditorProps>(
  function LazyMonacoEditor(props, _ref) {
    return (
      <Suspense fallback={<EditorSkeleton />}>
        <MonacoEditorLazy {...props} />
      </Suspense>
    );
  }
);

/**
 * Drop-in replacement for `import { DiffEditor } from "@monaco-editor/react"`.
 */
export const LazyDiffEditor = forwardRef<unknown, DiffEditorProps>(
  function LazyDiffEditor(props, _ref) {
    return (
      <Suspense fallback={<EditorSkeleton />}>
        <DiffEditorLazy {...props} />
      </Suspense>
    );
  }
);

// Re-export the OnMount type so consumers don't need to import from @monaco-editor/react
export type { OnMount } from "@monaco-editor/react";
