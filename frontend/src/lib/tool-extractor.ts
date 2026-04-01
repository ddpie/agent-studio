/**
 * Extract @tool function definitions from a deployment.zip's main.py.
 * Uses fflate for fast in-browser zip decompression.
 */
import { unzipSync } from "fflate";
import { readBinaryFromS3 } from "./s3-storage";

/**
 * Parse @tool blocks from main.py source code.
 * Returns the concatenated tool definitions string (everything from first @tool onward).
 */
export function parseToolDefinitions(source: string): string {
  const firstTool = source.indexOf("\n@tool");
  if (firstTool === -1) return "";

  // Include any imports before the first @tool that are after SYSTEM_PROMPT / app setup
  // We want just the @tool blocks and their preceding imports
  const toolSection = source.slice(firstTool + 1); // skip the leading \n
  return toolSection.trimEnd();
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
