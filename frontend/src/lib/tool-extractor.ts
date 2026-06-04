/**
 * Extract @tool function definitions from tool_definitions.py via Lambda API.
 */
import { fetchAgentFile } from "./api-client";

/**
 * Parse @tool blocks from main.py source code.
 * Extracts only @tool decorated functions and their preceding imports.
 * Stops at non-tool code like _stream_with_tools, @app.entrypoint, etc.
 */
export function parseToolDefinitions(source: string): string {
  const lines = source.split("\n");
  const toolBlocks: string[] = [];
  let inTool = false;
  let currentBlock: string[] = [];
  const preambleImports: string[] = [];
  let foundFirstTool = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trimStart();

    // @tool decorator starts a new tool block
    if (trimmed === "@tool") {
      if (inTool && currentBlock.length > 0) {
        toolBlocks.push(currentBlock.join("\n"));
      }
      inTool = true;
      foundFirstTool = true;
      currentBlock = [line];
      continue;
    }

    if (inTool) {
      // A top-level non-empty, non-indented line that isn't part of the function = end of tool
      if (trimmed && !line.startsWith(" ") && !line.startsWith("\t") && !trimmed.startsWith("def ") && !trimmed.startsWith("#") && !trimmed.startsWith("@")) {
        toolBlocks.push(currentBlock.join("\n"));
        inTool = false;
        currentBlock = [];
        // Don't continue — check if this line is another @tool or stop marker
        if (trimmed.startsWith("async def _") || trimmed.startsWith("def _") || trimmed.startsWith("@app.")) {
          break; // Hit template boilerplate, stop
        }
      } else {
        currentBlock.push(line);
      }
    } else if (foundFirstTool) {
      // Between tools — check for stop markers
      if (trimmed.startsWith("async def _") || trimmed.startsWith("def _") || trimmed.startsWith("@app.")) {
        break;
      }
    } else {
      // Before first @tool — collect import lines
      if (trimmed.startsWith("import ") || trimmed.startsWith("from ")) {
        preambleImports.push(line);
      }
    }
  }

  // Flush last tool block
  if (inTool && currentBlock.length > 0) {
    toolBlocks.push(currentBlock.join("\n"));
  }

  if (toolBlocks.length === 0) return "";

  return toolBlocks.join("\n\n\n").trimEnd();
}

/**
 * Extract tool_names from parsed tool definitions.
 * Only extracts names of @tool decorated functions, not all defs.
 */
export function extractToolNames(toolDefs: string): string {
  const names: string[] = [];
  const re = /@tool\s*\ndef\s+(\w+)\s*\(/g;
  let m;
  while ((m = re.exec(toolDefs)) !== null) {
    names.push(m[1]);
  }
  return names.join(",");
}

/**
 * Fetch tool_definitions.py from Lambda API and extract tool info.
 * Returns { tool_definitions, tool_names } or null if not available.
 */
export async function extractToolsFromDeployment(
  agentId: string
): Promise<{ tool_definitions: string; tool_names: string } | null> {
  try {
    const source = await fetchAgentFile(agentId, "tool_definitions.py");
    if (!source?.includes("@tool")) return null;

    // Strip the "from strands import tool" header, keep only @tool blocks
    const stripped = source.replace(/^from strands import tool\s*\n*/m, "").trim();
    if (!stripped?.includes("@tool")) return null;

    const tool_definitions = parseToolDefinitions(stripped) || stripped;
    const tool_names = extractToolNames(tool_definitions);
    return { tool_definitions, tool_names };
  } catch (err) {
    console.error("extractToolsFromDeployment error:", err);
    return null;
  }
}
