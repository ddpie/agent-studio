/**
 * Extract @tool function definitions from a deployment.zip's main.py.
 * Uses fflate for fast in-browser zip decompression.
 */
import { unzipSync } from "fflate";
import { readBinaryFromS3 } from "./s3-storage";

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
  let preambleImports: string[] = [];
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
 */
export function extractToolNames(toolDefs: string): string {
  const names: string[] = [];
  const re = /def\s+(\w+)\s*\(/g;
  let m;
  while ((m = re.exec(toolDefs)) !== null) {
    names.push(m[1]);
  }
  return names.join(",");
}

/**
 * Fetch deployment.zip from S3, extract main.py, parse @tool blocks.
 * Tries agentId path first, then falls back to base name path.
 * Returns { tool_definitions, tool_names } or null if extraction fails.
 */
export async function extractToolsFromDeployment(
  agentName: string
): Promise<{ tool_definitions: string; tool_names: string } | null> {
  try {
    // Try the given name first, then strip the suffix (e.g. "Agent-ey4RTBE1Wa" → "Agent")
    const baseName = agentName.replace(/[-_][A-Za-z0-9]{10}$/, "");
    const paths = [
      `agents/${agentName}/deployment.zip`,
      ...(baseName !== agentName ? [`agents/${baseName}/deployment.zip`] : []),
    ];

    let zipData: ArrayBuffer | null = null;
    for (const path of paths) {
      zipData = await readBinaryFromS3(path);
      if (zipData) break;
    }
    if (!zipData) return null;

    const files = unzipSync(new Uint8Array(zipData), {
      filter: (file) => file.name === "main.py",
    });

    const mainPy = files["main.py"];
    if (!mainPy) return null;

    const source = new TextDecoder().decode(mainPy);
    const tool_definitions = parseToolDefinitions(source);
    if (!tool_definitions) return null;

    const tool_names = extractToolNames(tool_definitions);
    return { tool_definitions, tool_names };
  } catch (err) {
    console.error("extractToolsFromDeployment error:", err);
    return null;
  }
}
