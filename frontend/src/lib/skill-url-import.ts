/**
 * Skill URL Import — fetch skill files from GitHub, ClawHub, or raw URLs.
 * Runs in the browser (no backend needed for CORS-friendly sources).
 */
import { unzipSync } from "fflate";
import { importSkillFromFiles } from "./skill-storage";

export interface UrlImportResult {
  id: string;
  name: string;
  description: string;
  filesCount: number;
  source: string;
}

/** Detect URL source type */
function detectSource(url: string): "clawhub" | "github-dir" | "github-file" | "gist" | "raw" {
  if (/clawhub\.ai\/|claw-hub\.net\//.test(url)) return "clawhub";
  if (/github\.com\/[^/]+\/[^/]+\/tree\//.test(url)) return "github-dir";
  if (/github\.com\/[^/]+\/[^/]+\/blob\//.test(url)) return "github-file";
  if (/gist\.github\.com\//.test(url)) return "gist";
  return "raw";
}

/** Fetch ClawHub skill as zip → extract files */
async function fetchClawHub(url: string): Promise<Record<string, string>> {
  const match = url.match(/(?:clawhub\.ai|claw-hub\.net)\/([^/?#]+\/[^/?#]+)/);
  if (!match) throw new Error("Cannot parse ClawHub slug from URL");
  const slug = match[1];

  const resp = await fetch(`https://clawhub.ai/api/v1/download?slug=${slug}`);
  if (!resp.ok) throw new Error(`ClawHub API error: ${resp.status}`);

  const buffer = await resp.arrayBuffer();
  const unzipped = unzipSync(new Uint8Array(buffer));

  const files: Record<string, string> = {};
  for (const [path, data] of Object.entries(unzipped)) {
    if (!path || path.endsWith("/")) continue;
    // Strip common prefix directory (e.g., "skill-name/SKILL.md" → "SKILL.md")
    const parts = path.split("/");
    const relPath = parts.length > 1 ? parts.slice(1).join("/") : path;
    if (!relPath || relPath.startsWith(".") || relPath.startsWith("__")) continue;
    try {
      files[relPath] = new TextDecoder().decode(data);
    } catch {
      // Skip binary files
    }
  }
  return files;
}

/** Fetch all files from a GitHub directory recursively */
async function fetchGitHubDir(url: string): Promise<Record<string, string>> {
  const match = url.match(/github\.com\/([^/]+)\/([^/]+)\/tree\/([^/]+)(?:\/(.*))?/);
  if (!match) throw new Error("Cannot parse GitHub directory URL");
  const [, owner, repo, branch, path] = match;
  const dirPath = (path || "").replace(/\/$/, "");

  const files: Record<string, string> = {};
  await fetchGitHubDirRecursive(owner, repo, branch, dirPath, dirPath, files);
  return files;
}

async function fetchGitHubDirRecursive(
  owner: string, repo: string, branch: string,
  currentPath: string, basePath: string,
  files: Record<string, string>,
): Promise<void> {
  const apiUrl = `https://api.github.com/repos/${owner}/${repo}/contents/${currentPath}?ref=${branch}`;
  const resp = await fetch(apiUrl, { headers: { Accept: "application/vnd.github.v3+json" } });
  if (!resp.ok) throw new Error(`GitHub API error: ${resp.status}`);

  const items: Array<{ type: string; name: string; path: string; download_url: string | null }> = await resp.json();

  for (const item of items) {
    if (item.name.startsWith(".") || item.name.startsWith("__")) continue;

    if (item.type === "file" && item.download_url) {
      const relPath = basePath ? item.path.slice(basePath.length + 1) : item.path;
      try {
        const fileResp = await fetch(item.download_url);
        if (fileResp.ok) {
          files[relPath] = await fileResp.text();
        }
      } catch { /* skip failed files */ }
    } else if (item.type === "dir") {
      await fetchGitHubDirRecursive(owner, repo, branch, item.path, basePath, files);
    }
  }
}

/** Fetch a single file from GitHub (blob URL → raw) */
async function fetchGitHubFile(url: string): Promise<string> {
  const match = url.match(/github\.com\/([^/]+)\/([^/]+)\/blob\/([^/]+)\/(.*)/);
  if (!match) throw new Error("Cannot parse GitHub file URL");
  const [, owner, repo, branch, path] = match;
  const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`;
  const resp = await fetch(rawUrl);
  if (!resp.ok) throw new Error(`GitHub raw fetch error: ${resp.status}`);
  return resp.text();
}

/** Fetch all files from a GitHub Gist */
async function fetchGist(url: string): Promise<Record<string, string>> {
  const match = url.match(/gist\.github\.com\/(?:[^/]+\/)?([a-f0-9]+)/);
  if (!match) throw new Error("Cannot parse Gist URL");
  const gistId = match[1];

  const resp = await fetch(`https://api.github.com/gists/${gistId}`, {
    headers: { Accept: "application/vnd.github.v3+json" },
  });
  if (!resp.ok) throw new Error(`Gist API error: ${resp.status}`);

  const data: { files: Record<string, { filename: string; content: string }> } = await resp.json();
  const files: Record<string, string> = {};
  for (const [, file] of Object.entries(data.files)) {
    files[file.filename] = file.content;
  }
  return files;
}

/** Fetch raw URL content */
async function fetchRawUrl(url: string): Promise<string> {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Fetch error: ${resp.status}`);
  return resp.text();
}

/**
 * Import a skill from a URL. Detects source type and fetches accordingly.
 * Returns the imported skill info, or throws on error.
 */
export async function importSkillFromUrl(url: string): Promise<UrlImportResult> {
  const source = detectSource(url);
  let files: Record<string, string> = {};

  if (source === "clawhub") {
    files = await fetchClawHub(url);
  } else if (source === "github-dir") {
    files = await fetchGitHubDir(url);
  } else if (source === "gist") {
    files = await fetchGist(url);
  } else if (source === "github-file") {
    const content = await fetchGitHubFile(url);
    const fileName = url.split("/").pop() || "SKILL.md";
    files = { [fileName === "SKILL.md" ? "SKILL.md" : fileName]: content };
  } else {
    const content = await fetchRawUrl(url);
    files = { "SKILL.md": content };
  }

  if (Object.keys(files).length === 0) {
    throw new Error("No files found at the given URL");
  }

  // If no SKILL.md but has a single .md file, rename it
  if (!files["SKILL.md"]) {
    const mdFiles = Object.keys(files).filter(f => f.endsWith(".md"));
    if (mdFiles.length === 1) {
      files["SKILL.md"] = files[mdFiles[0]];
      delete files[mdFiles[0]];
    } else if (!mdFiles.length) {
      // Wrap first file as SKILL.md
      const firstKey = Object.keys(files)[0];
      const nameFromUrl = url.split("/").pop()?.replace(/\.[^.]+$/, "") || "imported-skill";
      files["SKILL.md"] = `---\nname: "${nameFromUrl}"\ndescription: "Imported from URL"\ntype: "prompt"\nsource: "url"\nuser-invocable: true\n---\n\n${files[firstKey]}`;
      delete files[firstKey];
    }
  }

  const result = await importSkillFromFiles(files);
  if (!result) throw new Error("Failed to write skill to storage");

  return {
    id: result.id,
    name: result.name,
    description: result.description,
    filesCount: Object.keys(files).length,
    source,
  };
}
