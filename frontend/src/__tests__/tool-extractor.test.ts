import { describe, it, expect } from "vitest";
import { parseToolDefinitions, extractToolNames } from "../lib/tool-extractor";

// ── parseToolDefinitions ────────────────────────────────────────

describe("parseToolDefinitions", () => {
  it("extracts a single @tool function", () => {
    const source = `
import json

@tool
def greet(name: str = "") -> str:
    """Say hello."""
    return f"Hello {name}"

async def _stream_with_tools(agent, prompt):
    pass

@app.entrypoint
async def main():
    pass
`.trim();

    const result = parseToolDefinitions(source);
    expect(result).toContain("@tool");
    expect(result).toContain("def greet");
    // Parser stops at "async def _" (non-indented, non-@ line)
    expect(result).not.toContain("_stream_with_tools");
    expect(result).not.toContain("@app.entrypoint");
  });

  it("extracts multiple @tool functions", () => {
    const source = `
import json

@tool
def alpha() -> str:
    """A."""
    return "a"

@tool
def beta(x: int = 0) -> str:
    """B."""
    return str(x)

async def _stream_with_tools(agent, prompt):
    pass

@app.entrypoint
async def main():
    pass
`.trim();

    const result = parseToolDefinitions(source);
    expect(result).toContain("def alpha");
    expect(result).toContain("def beta");
    expect(result).not.toContain("_stream_with_tools");
  });

  it("returns empty string when no @tool found", () => {
    const source = `
import json

def helper():
    pass

@app.entrypoint
async def main():
    pass
`.trim();

    expect(parseToolDefinitions(source)).toBe("");
  });

  it("stops at async def _ (private function)", () => {
    const source = `
@tool
def search(q: str = "") -> str:
    """Search."""
    return q

async def _stream_with_tools(agent, prompt):
    pass
`.trim();

    const result = parseToolDefinitions(source);
    expect(result).toContain("def search");
    expect(result).not.toContain("_stream_with_tools");
  });

  it("includes def _ in tool block (parser treats all def lines as tool continuation)", () => {
    // Known behavior: "def _helper" starts with "def " so the parser
    // doesn't recognize it as a stop marker while inside a tool block.
    // Only "async def _" works as a stop marker (it starts with "async", not "def").
    const source = `
@tool
def foo() -> str:
    """F."""
    return ""

def _helper():
    pass
`.trim();

    const result = parseToolDefinitions(source);
    expect(result).toContain("def foo");
    // This documents current behavior — def _helper is included
    expect(result).toContain("_helper");
  });

  it("handles @tool with multi-line body", () => {
    const source = `
@tool
def complex(data: str = "") -> str:
    """Process data.

    Multi-line docstring here.
    """
    result = []
    for item in data.split(","):
        if item.strip():
            result.append(item.upper())
    return json.dumps(result)
`.trim();

    const result = parseToolDefinitions(source);
    expect(result).toContain("Multi-line docstring");
    expect(result).toContain("result.append");
  });

  it("handles file with only imports and no tools", () => {
    const source = `
import os
import json
from pathlib import Path
`.trim();

    expect(parseToolDefinitions(source)).toBe("");
  });

  it("handles empty string", () => {
    expect(parseToolDefinitions("")).toBe("");
  });

  it("handles @tool at end of file without trailing newline", () => {
    const source = `@tool
def last() -> str:
    """Last tool."""
    return "done"`;

    const result = parseToolDefinitions(source);
    expect(result).toContain("def last");
    expect(result).toContain('"done"');
  });

  it("handles consecutive @tool with no blank lines between", () => {
    const source = `@tool
def a() -> str:
    """A."""
    return "a"
@tool
def b() -> str:
    """B."""
    return "b"`;

    const result = parseToolDefinitions(source);
    expect(result).toContain("def a");
    expect(result).toContain("def b");
  });
});

// ── extractToolNames ────────────────────────────────────────────

describe("extractToolNames", () => {
  it("extracts single function name", () => {
    const defs = `@tool\ndef greet(name: str = "") -> str:\n    return ""`;
    expect(extractToolNames(defs)).toBe("greet");
  });

  it("extracts multiple function names comma-separated", () => {
    const defs = `@tool\ndef alpha() -> str:\n    return ""\n\n@tool\ndef beta() -> str:\n    return ""`;
    expect(extractToolNames(defs)).toBe("alpha,beta");
  });

  it("returns empty string for no functions", () => {
    expect(extractToolNames("")).toBe("");
    expect(extractToolNames("# just a comment")).toBe("");
  });

  it("handles async def (should not match — tools are sync)", () => {
    // extractToolNames uses /def\s+(\w+)\s*\(/ which won't match "async def" directly
    // but the regex matches "def" inside "async def" — verify actual behavior
    const defs = `async def helper():\n    pass`;
    const names = extractToolNames(defs);
    // "def" appears in "async def" so regex will match — this documents current behavior
    expect(names).toBe("helper");
  });
});
