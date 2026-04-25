/**
 * editor-bridge — module-level singleton that lets the edit-assistant push
 * AI-generated skill file contents into the currently-mounted `SkillEditorView`.
 *
 * Why not a Zustand field? A write handler is not observable state — no
 * component needs to re-render when it changes. Putting it in the store
 * would be service-locator-via-Zustand. A module singleton (same pattern
 * edit-assistant-store already uses for `_abortController`) is the minimal
 * fit: one writer, one reader, lifetime tied to the editor component.
 *
 * Flow:
 *   SkillEditorView mounts  → editorBridge.register(write)
 *   SkillEditorView unmounts → returned disposer called
 *   edit-assistant receives AI `__field_value:skill:…` block
 *                           → setPendingSkillFiles (store: drives diff/deploy)
 *                           → editorBridge.write    (editor: drives Monaco +
 *                                                    save button + discard)
 *
 * The dual-write here (store + editor) mirrors what the user's own keystroke
 * path already does via handleEditorChange → (editor internal) +
 * updatePendingSkillFile. Don't collapse one into the other without a full
 * refactor of useFileEditor — SkillDetail also consumes the hook and doesn't
 * use agent-edit-store.
 */

export type EditorWriteHandler = (
  skillId: string,
  path: string,
  content: string,
) => void;

let _handler: EditorWriteHandler | null = null;

export const editorBridge = {
  /**
   * Install the write handler. Returns a disposer that only clears the
   * handler if it's still the same one — safe against out-of-order mount/
   * unmount under React StrictMode double-invoke.
   */
  register(fn: EditorWriteHandler): () => void {
    _handler = fn;
    return () => {
      if (_handler === fn) _handler = null;
    };
  },

  /** Push a file update to whichever editor is currently mounted. No-op if none. */
  write(skillId: string, path: string, content: string): void {
    _handler?.(skillId, path, content);
  },

  /** True when an editor is mounted. Callers can use this to decide whether
   *  to show a "open editor to see changes" hint. */
  hasHandler(): boolean {
    return _handler !== null;
  },
};
