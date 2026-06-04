/**
 * Skill URL Import — fetch skill files from GitHub, ClawHub, or raw URLs.
 * Runs in the browser (no backend needed for CORS-friendly sources).
 */
import { unzipSync } from "fflate";
import { importSkillFromFiles } from "./skill-storage";
import { invokeMetaAgent } from "./agentcore-client";

export interface UrlImportResult {
  id: string;
  name: string;
  description: string;
  filesCount: number;
  source: string;
}

type ProgressFn = (key: string, params?: Record<string, string | number>) => void;

/** Detect URL source type */
function detectSource(url: string): "clawhub" | "github-dir" | "github-file" | "gist" | "raw" {
  if (/clawhub\.ai\/|claw-hub\.net\//.test(url)) return "clawhub";
  if (/github\.com\/[^/]+\/[^/]+\/tree\//.test(url)) return "github-dir";
  if (/github\.com\/[^/]+\/[^/]+\/blob\//.test(url)) return "github-file";
  if (url.includes('gist.github.com/')) return "gist";
  return "raw";
}

/** True for obvious noise (caches, VCS metadata, OS cruft, build artifacts).
 *
 * Earlier versions naively skipped any path with a basename starting with
 * ``__``, which silently stripped legitimate Python package markers
 * (``__init__.py``, ``__main__.py``) and broke every scripted skill whose
 * code was organized as a Python package — callers saw ``cannot import
 * name 'X' from partially initialized module`` at runtime because the
 * thin wrapper ``svg_to_pptx.py`` resolved ``from svg_to_pptx import main``
 * to itself instead of the package that was never written to S3.
 *
 * Mirrors meta-agent/tools/import_skill._should_skip_path so the frontend
 * and backend import paths don't disagree on what a "noise" file is.
 */
function shouldSkipPath(relPath: string): boolean {
  if (!relPath) return true;
  if (relPath.startsWith(".")) return true;
  const parts = relPath.replace(/\\/g, "/").split("/");
  const noiseDirs = new Set([
    "__pycache__", "__MACOSX", ".git", ".github", ".idea", "node_modules",
  ]);
  if (parts.some(p => noiseDirs.has(p))) return true;
  const leaf = parts[parts.length - 1];
  if (leaf.endsWith(".pyc") || leaf.endsWith(".pyo")) return true;
  if (leaf === ".DS_Store") return true;
  return false;
}

/** Fetch ClawHub skill as zip → extract files */
async function fetchClawHub(url: string, onProgress?: ProgressFn): Promise<Record<string, string>> {
  const match = /(?:clawhub\.ai|claw-hub\.net)\/([^/?#]+\/[^/?#]+)/.exec(url);
  if (!match) throw new Error("Cannot parse ClawHub slug from URL");
  const slug = match[1];

  onProgress?.("skills.urlDownloadingZip");
  const resp = await fetch(`https://clawhub.ai/api/v1/download?slug=${slug}`);
  if (!resp.ok) throw new Error(`ClawHub API error: ${resp.status}`);

  const buffer = await resp.arrayBuffer();
  onProgress?.("skills.urlExtracting");
  const unzipped = unzipSync(new Uint8Array(buffer));

  const files: Record<string, string> = {};
  for (const [path, data] of Object.entries(unzipped)) {
    if (!path || path.endsWith("/")) continue;
    const parts = path.split("/");
    const relPath = parts.length > 1 ? parts.slice(1).join("/") : path;
    if (shouldSkipPath(relPath)) continue;
    try {
      files[relPath] = new TextDecoder().decode(data);
    } catch { /* skip binary */ }
  }
  onProgress?.("skills.urlDownloaded", { current: Object.keys(files).length, total: Object.keys(files).length });
  return files;
}

/** Fetch all files from a GitHub directory.
 * Strategy: Trees API (1 call) + raw.githubusercontent.com (CORS ok, no rate limit).
 * Fallback: Meta-Agent backend if API rate limited.
 */
async function fetchGitHubDir(url: string, onProgress?: ProgressFn): Promise<Record<string, string>> {
  const match = /github\.com\/([^/]+)\/([^/]+)\/tree\/([^/]+)(?:\/(.*))?/.exec(url);
  if (!match) throw new Error("Cannot parse GitHub directory URL");
  const [, owner, repo, branch, path] = match;
  const basePath = (path || "").replace(/\/$/, "");

  // Try Trees API first (1 API call)
  onProgress?.("skills.urlScanning");
  const treeResp = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/git/trees/${branch}?recursive=1`,
    { headers: { Accept: "application/vnd.github.v3+json" } }
  );

  if (!treeResp.ok) {
    // Rate limited — fallback to Meta-Agent backend
    onProgress?.("skills.urlFetchingBackend");
    return fetchGitHubDirViaBackend(url, onProgress);
  }

  const treeData: { tree: { path: string; type: string; size?: number }[] } = await treeResp.json();

  const fileEntries = treeData.tree.filter(item => {
    if (item.type !== "blob") return false;
    if (basePath && !item.path.startsWith(basePath + "/")) return false;
    const relPath = basePath ? item.path.slice(basePath.length + 1) : item.path;
    if (shouldSkipPath(relPath)) return false;
    return true;
  });

  const total = fileEntries.length;
  onProgress?.("skills.urlFoundFiles", { total });

  // Parallel download from raw.githubusercontent.com (CORS ok, no rate limit)
  let completed = 0;
  const files: Record<string, string> = {};

  await Promise.all(fileEntries.map(async (item) => {
    const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${item.path}`;
    try {
      const resp = await fetch(rawUrl);
      if (resp.ok) {
        const relPath = basePath ? item.path.slice(basePath.length + 1) : item.path;
        files[relPath] = await resp.text();
      }
    } catch { /* skip */ }
    completed++;
    onProgress?.("skills.urlDownloaded", { current: completed, total });
  }));

  return files;
}

/** Fallback: use Meta-Agent to import GitHub dir (server-side, no CORS) */
async function fetchGitHubDirViaBackend(url: string, onProgress?: ProgressFn): Promise<Record<string, string>> {
  onProgress?.("skills.urlFetchingBackend");

  let fullText = "";
  for await (const chunk of invokeMetaAgent(
    `Import the skill from this GitHub URL using the import_skill tool with url="${url}". Return only the JSON result.`,
    [],
  )) {
    fullText += chunk;
  }

  const idMatch = /"skill_id"\s*:\s*"([^"]+)"/.exec(fullText);
  if (!idMatch) throw new Error("Backend import failed — check Meta-Agent logs");

  return { __meta_agent_imported: idMatch[1] };
}

/** Fetch a single file from GitHub (blob URL → raw) */
async function fetchGitHubFile(url: string): Promise<string> {
  const match = /github\.com\/([^/]+)\/([^/]+)\/blob\/([^/]+)\/(.*)/.exec(url);
  if (!match) throw new Error("Cannot parse GitHub file URL");
  const [, owner, repo, branch, path] = match;
  const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}`;
  const resp = await fetch(rawUrl);
  if (!resp.ok) throw new Error(`GitHub raw fetch error: ${resp.status}`);
  return resp.text();
}

/** Fetch all files from a GitHub Gist */
async function fetchGist(url: string): Promise<Record<string, string>> {
  const match = /gist\.github\.com\/(?:[^/]+\/)?([a-f0-9]+)/.exec(url);
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
export async function importSkillFromUrl(url: string, onProgress?: ProgressFn): Promise<UrlImportResult> {
  const source = detectSource(url);
  let files: Record<string, string> = {};

  onProgress?.("skills.urlDetected", { source });

  if (source === "clawhub") {
    onProgress?.("skills.urlFetching", { source: "ClawHub" });
    files = await fetchClawHub(url, onProgress);
  } else if (source === "github-dir") {
    onProgress?.("skills.urlFetching", { source: "GitHub" });
    files = await fetchGitHubDir(url, onProgress);
  } else if (source === "gist") {
    onProgress?.("skills.urlFetching", { source: "Gist" });
    files = await fetchGist(url);
  } else if (source === "github-file") {
    onProgress?.("skills.urlFetching", { source: "GitHub" });
    const content = await fetchGitHubFile(url);
    const fileName = url.split("/").pop() || "SKILL.md";
    files = { [fileName === "SKILL.md" ? "SKILL.md" : fileName]: content };
  } else {
    onProgress?.("skills.urlFetching", { source: "URL" });
    const content = await fetchRawUrl(url);
    files = { "SKILL.md": content };
  }

  if (Object.keys(files).length === 0) {
    throw new Error("No files found at the given URL");
  }

  // Meta-Agent handled the import (GitHub dir) — skill already in S3
  if (files.__meta_agent_imported) {
    const skillId = files.__meta_agent_imported;
    // Refresh skill list to get name/description
    const { listSkills } = await import("./skill-storage");
    const skills = await listSkills();
    const skill = skills.find(s => s.id === skillId);
    return {
      id: skillId,
      name: skill?.name || "imported-skill",
      description: skill?.description || "",
      filesCount: 0,
      source,
    };
  }

  // If no SKILL.md but has a single .md file, rename it
  if (!files["SKILL.md"]) {
    const mdFiles = Object.keys(files).filter(f => f.endsWith(".md"));
    if (mdFiles.length === 1) {
      files["SKILL.md"] = files[mdFiles[0]];
      delete files[mdFiles[0]];
    } else if (!mdFiles.length) {
      const firstKey = Object.keys(files)[0];
      const nameFromUrl = url.split("/").pop()?.replace(/\.[^.]+$/, "") || "imported-skill";
      files["SKILL.md"] = `---\nname: "${nameFromUrl}"\ndescription: "Imported from URL"\ntype: "prompt"\nsource: "url"\nuser-invocable: true\n---\n\n${files[firstKey]}`;
      delete files[firstKey];
    }
  }

  onProgress?.("skills.urlWriting", { count: Object.keys(files).length });

  const result = await importSkillFromFiles(files, (current, total) => {
    onProgress?.("skills.urlWritingProgress", { current, total });
  });
  if (!result) throw new Error("Failed to write skill to storage");

  return {
    id: result.id,
    name: result.name,
    description: result.description,
    filesCount: Object.keys(files).length,
    source,
  };
}
