import { validatePython } from "./validators/python-validator";
import { validateShell } from "./validators/shell-validator";
import type * as MonacoNS from "monaco-editor";

export interface Diagnostic {
  line: number;
  col: number;
  message: string;
  severity: number;
}

export function lint(filePath: string, content: string): Diagnostic[] {
  if (filePath.endsWith(".py")) return validatePython(content);
  if (filePath.endsWith(".sh") || filePath.endsWith(".bash")) return validateShell(content);
  return [];
}

export function applyLintMarkers(
  monaco: typeof MonacoNS,
  model: MonacoNS.editor.ITextModel,
  filePath: string,
  content: string,
): void {
  if (!filePath.endsWith(".py") && !filePath.endsWith(".sh") && !filePath.endsWith(".bash")) return;
  const owner = filePath.endsWith(".py") ? "python-lint" : "shell-lint";
  const diagnostics = lint(filePath, content);
  const markers = diagnostics.map((d) => ({
    startLineNumber: d.line,
    endLineNumber: d.line,
    startColumn: d.col,
    endColumn: 1000,
    message: d.message,
    severity: d.severity as unknown as MonacoNS.MarkerSeverity,
  }));
  monaco.editor.setModelMarkers(model, owner, markers);
}
