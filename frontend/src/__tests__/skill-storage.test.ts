import { describe, it, expect } from "vitest";
import { parseFrontmatter, wrapWithFrontmatter } from "../lib/skill-storage";

// ── parseFrontmatter ────────────────────────────────────────────

describe("parseFrontmatter", () => {
  it("parses standard YAML frontmatter", () => {
    const content = `---
name: "data-analyzer"
description: "Analyze CSV data"
type: "prompt"
---

# Data Analyzer
Some content here.`;

    const result = parseFrontmatter(content);
    expect(result).toEqual({ name: "data-analyzer", description: "Analyze CSV data" });
  });

  it("parses frontmatter without quotes", () => {
    const content = `---
name: my-skill
description: does stuff
---

Body`;

    const result = parseFrontmatter(content);
    expect(result).toEqual({ name: "my-skill", description: "does stuff" });
  });

  it("parses frontmatter with single quotes", () => {
    const content = `---
name: 'quoted-skill'
description: 'a description'
---

Body`;

    const result = parseFrontmatter(content);
    expect(result).toEqual({ name: "quoted-skill", description: "a description" });
  });

  it("returns null when no frontmatter delimiter", () => {
    expect(parseFrontmatter("# Just markdown\nNo frontmatter")).toBeNull();
  });

  it("returns null when only one delimiter", () => {
    expect(parseFrontmatter("---\nname: broken")).toBeNull();
  });

  it("returns null when name is missing", () => {
    const content = `---
description: "no name here"
---

Body`;

    expect(parseFrontmatter(content)).toBeNull();
  });

  it("handles empty description", () => {
    const content = `---
name: minimal
description:
---

Body`;

    const result = parseFrontmatter(content);
    expect(result).toEqual({ name: "minimal", description: "" });
  });

  it("handles description missing entirely", () => {
    const content = `---
name: only-name
type: prompt
---

Body`;

    const result = parseFrontmatter(content);
    expect(result).toEqual({ name: "only-name", description: "" });
  });

  it("returns null for empty string", () => {
    expect(parseFrontmatter("")).toBeNull();
  });
});

// ── wrapWithFrontmatter ─────────────────────────────────────────

describe("wrapWithFrontmatter", () => {
  it("wraps plain markdown with frontmatter", () => {
    const result = wrapWithFrontmatter("# My Skill\nDo things.", "my-skill", "does things");
    expect(result).toContain('name: "my-skill"');
    expect(result).toContain('description: "does things"');
    expect(result).toContain("# My Skill");
    expect(result).toContain("Do things.");
  });

  it("starts with --- delimiter", () => {
    const result = wrapWithFrontmatter("body", "s", "d");
    expect(result.startsWith("---\n")).toBe(true);
  });

  it("includes required fields", () => {
    const result = wrapWithFrontmatter("body", "test", "desc");
    expect(result).toContain('type: "prompt"');
    expect(result).toContain('source: "imported"');
    expect(result).toContain("user-invocable: true");
  });

  it("trims trailing whitespace from content", () => {
    const result = wrapWithFrontmatter("body text   \n\n  ", "s", "d");
    expect(result).toContain("body text");
    expect(result).not.toContain("body text   \n\n  ");
  });
});
