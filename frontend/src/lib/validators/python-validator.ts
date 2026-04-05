/**
 * Python validation — Pyodide compile() if available, fallback to bracket balance.
 */
import { isPyodideReady, checkPythonSyntax } from "../pyodide-checker";

export interface PythonMarker {
  line: number;
  col: number;
  message: string;
  severity: number;
}

export function validatePython(code: string): PythonMarker[] {
  if (isPyodideReady()) {
    return checkPythonSyntax(code).map(e => ({
      line: e.line,
      col: e.col || 1,
      message: e.msg,
      severity: 8,
    }));
  }
  const markers: PythonMarker[] = [];
  const lines = code.split("\n");
  let parens = 0, brackets = 0, braces = 0;
  for (const line of lines) {
    for (const ch of line) {
      if (ch === "(") parens++; else if (ch === ")") parens--;
      else if (ch === "[") brackets++; else if (ch === "]") brackets--;
      else if (ch === "{") braces++; else if (ch === "}") braces--;
    }
  }
  if (parens !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced parentheses", severity: 8 });
  if (brackets !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced brackets", severity: 8 });
  if (braces !== 0) markers.push({ line: lines.length, col: 1, message: "Unbalanced braces", severity: 8 });
  return markers;
}
