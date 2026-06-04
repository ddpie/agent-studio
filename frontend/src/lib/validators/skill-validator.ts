/**
 * Skill SKILL.md validation — frontmatter structure and file references.
 */
import type { ValidationResult } from "../types/validation";

export function validateSkill(
  skillMdContent: string,
  virtualFiles: string[],
  pendingDeletes: Set<string>,
): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (!skillMdContent.startsWith("---")) {
    errors.push("SKILL.md must start with YAML frontmatter (---)");
    return { valid: false, errors, warnings };
  }
  const parts = skillMdContent.split("---", 3);
  if (parts.length < 3) {
    errors.push("SKILL.md frontmatter is incomplete (missing closing ---)");
    return { valid: false, errors, warnings };
  }

  const fm = parts[1].trim();
  const fields: Record<string, string> = {};
  for (const line of fm.split("\n")) {
    const match = /^(\w[\w-]*):\s*(.*)/.exec(line);
    if (match) fields[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
  }

  if (!fields.name) errors.push("Missing required field: name");
  if (!fields.description) warnings.push("Missing field: description (recommended)");
  if (fields.name && !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(fields.name)) {
    warnings.push("Skill name should be alphanumeric with hyphens/underscores");
  }

  if (fields.files || fm.includes("files:")) {
    const fileLines = fm.split("\n").filter(l => l.trim().startsWith("- "));
    for (const fl of fileLines) {
      const ref = fl.trim().replace(/^-\s*/, "").trim();
      if (ref && !virtualFiles.includes(ref) || pendingDeletes.has(ref)) {
        errors.push(`Referenced file not found: ${ref}`);
      }
    }
  }

  const body = parts[2].trim();
  if (!body) warnings.push("SKILL.md body is empty");

  return { valid: errors.length === 0, errors, warnings };
}
