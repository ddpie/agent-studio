/**
 * Python syntax checker using Pyodide (CPython compiled to WASM).
 * Lazy-loads Pyodide on first use (~10MB), provides real compile() checking.
 */

let pyodide: { runPython: (code: string) => unknown } | null = null;
let loading = false;
let loadFailed = false;

export interface PythonError {
  line: number;
  col: number;
  msg: string;
}

/** Start loading Pyodide in background. Call early to warm up. */
export function preloadPyodide() {
  if (pyodide || loading || loadFailed) return;
  loading = true;
  (async () => {
    try {
      // Dynamic import — Vite will handle this
      const { loadPyodide: load } = await import("pyodide");
      pyodide = await load({
        indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/",
      }) as typeof pyodide;
      console.log("[Pyodide] Loaded successfully");
    } catch (err) {
      console.warn("[Pyodide] Failed to load:", err);
      loadFailed = true;
    } finally {
      loading = false;
    }
  })();
}

/** Check if Pyodide is ready */
export function isPyodideReady(): boolean {
  return pyodide !== null;
}

/** Check Python syntax using compile(). Returns errors or empty array. */
export function checkPythonSyntax(code: string): PythonError[] {
  if (!pyodide) return [];
  try {
    // Escape the code for embedding in Python string
    const escaped = code.replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/\n/g, "\\n");
    const result = pyodide.runPython(
      `import json\n` +
      `_code = '${escaped}'\n` +
      `_errors = []\n` +
      `try:\n` +
      `    compile(_code, '<tool>', 'exec')\n` +
      `except SyntaxError as _e:\n` +
      `    _errors.append({"line": _e.lineno or 1, "col": _e.offset or 0, "msg": str(_e.msg)})\n` +
      `except Exception as _e:\n` +
      `    _errors.append({"line": 1, "col": 0, "msg": str(_e)})\n` +
      `json.dumps(_errors)`
    );
    return JSON.parse(String(result));
  } catch (err) {
    console.warn("[Pyodide] Check failed:", err);
    return [];
  }
}
