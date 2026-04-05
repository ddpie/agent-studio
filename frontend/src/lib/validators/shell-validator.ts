/**
 * Shell script validation — quotes, if/fi, do/done, case/esac balance.
 */

export interface ShellMarker {
  line: number;
  col: number;
  message: string;
  severity: number;
}

export function validateShell(code: string): ShellMarker[] {
  const markers: ShellMarker[] = [];
  const lines = code.split("\n");

  let inSingle = false, inDouble = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    for (let j = 0; j < line.length; j++) {
      const ch = line[j];
      if (ch === "\\" && !inSingle) { j++; continue; }
      if (ch === "'" && !inDouble) inSingle = !inSingle;
      else if (ch === '"' && !inSingle) inDouble = !inDouble;
    }
  }
  if (inSingle) markers.push({ line: lines.length, col: 1, message: "Unterminated single quote", severity: 8 });
  if (inDouble) markers.push({ line: lines.length, col: 1, message: "Unterminated double quote", severity: 8 });

  let ifCount = 0, fiCount = 0, doCount = 0, doneCount = 0, caseCount = 0, esacCount = 0;
  for (const line of lines) {
    const words = line.trim().replace(/#.*$/, "").split(/\s+|;/);
    for (const w of words) {
      if (w === "if" || w === "elif") ifCount++;
      else if (w === "fi") fiCount++;
      else if (w === "do") doCount++;
      else if (w === "done") doneCount++;
      else if (w === "case") caseCount++;
      else if (w === "esac") esacCount++;
    }
  }
  if (ifCount !== fiCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced if/fi (${ifCount} if vs ${fiCount} fi)`, severity: 8 });
  if (doCount !== doneCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced do/done (${doCount} do vs ${doneCount} done)`, severity: 8 });
  if (caseCount !== esacCount) markers.push({ line: lines.length, col: 1, message: `Unbalanced case/esac`, severity: 8 });

  return markers;
}
