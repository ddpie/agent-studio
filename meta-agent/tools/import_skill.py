"""import_skill — Import a skill from URL (ClawHub, GitHub, raw) or raw content."""

import io
import json
import re
import uuid
import zipfile

import boto3
from strands import tool

from config import REGION, S3_BUCKET


def _parse_frontmatter(content: str) -> dict | None:
    """Parse YAML frontmatter from SKILL.md content. Returns dict or None.

    Uses ``yaml.safe_load`` so list/dict values (notably ``requires:``)
    survive parsing. Old string-only parser silently flattened lists to
    their string repr, which broke any downstream ``requires:`` handling.
    """
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        import yaml
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    return meta if meta.get("name") else None


def _wrap_with_frontmatter(content: str, name: str, description: str, source: str = "imported") -> str:
    """Wrap plain markdown with AgentSkills.io frontmatter."""
    return f"""---
name: "{name}"
description: "{description}"
type: "prompt"
source: "{source}"
user-invocable: true
---

{content.strip()}
"""


def _http_get(url: str, accept: str = "*/*", binary: bool = False):
    """Simple HTTP GET with User-Agent header. Rejects private/metadata IPs (incl. redirects)."""
    import urllib.request
    from url_validation import safe_urlopen
    req = urllib.request.Request(url, headers={
        "User-Agent": "AgentStudio/1.0",
        "Accept": accept,
    })
    with safe_urlopen(req, timeout=30) as resp:
        return resp.read() if binary else resp.read().decode("utf-8")


def _fetch_clawhub(url: str) -> dict[str, str]:
    """Fetch skill files from ClawHub API. Returns {path: content}."""
    # Extract slug from URL: clawhub.ai/{author}/{slug} or claw-hub.net/...
    match = re.search(r'(?:clawhub\.ai|claw-hub\.net)/([^/?#]+/[^/?#]+)', url)
    if not match:
        raise ValueError(f"Cannot parse ClawHub slug from URL: {url}")
    slug = match.group(1)

    # Download zip
    api_url = f"https://clawhub.ai/api/v1/download?slug={slug}"
    data = _http_get(api_url, binary=True)

    # Extract files from zip
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            # Strip leading directory if all files share one (e.g., "skill-name/SKILL.md")
            parts = name.split("/", 1)
            if len(parts) == 2 and parts[0] and not parts[0].startswith("."):
                # Check if this is a common prefix
                name = parts[1] if parts[1] else parts[0]
            if not name or name.startswith(".") or name.startswith("__"):
                continue
            try:
                content = zf.read(info.filename).decode("utf-8")
                files[name] = content
            except UnicodeDecodeError:
                continue  # Skip binary files

    if not files:
        raise ValueError("ClawHub zip is empty or contains no readable files")
    return files


def _fetch_github_dir(url: str) -> dict[str, str]:
    """Fetch all files from a GitHub directory. Returns {path: content}."""
    # Parse: github.com/{owner}/{repo}/tree/{branch}/{path}
    match = re.match(
        r'https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)(?:/(.*))?', url
    )
    if not match:
        raise ValueError(f"Cannot parse GitHub directory URL: {url}")
    owner, repo, branch, path = match.groups()
    path = (path or "").rstrip("/")

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    items = json.loads(_http_get(api_url, accept="application/vnd.github.v3+json"))

    files = {}
    _fetch_github_dir_recursive(items, files, owner, repo, branch, path)
    return files


def _fetch_github_dir_recursive(items: list, files: dict, owner: str, repo: str, branch: str, base_path: str):
    """Recursively fetch files from GitHub API contents response."""
    for item in items:
        if item["type"] == "file":
            # Compute relative path from base
            full_path = item["path"]
            rel_path = full_path[len(base_path):].lstrip("/") if base_path else full_path
            if rel_path.startswith(".") or rel_path.startswith("__"):
                continue
            try:
                content = _http_get(item["download_url"])
                files[rel_path] = content
            except Exception:
                continue
        elif item["type"] == "dir":
            # Recurse into subdirectory
            try:
                sub_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{item['path']}?ref={branch}"
                sub_items = json.loads(_http_get(sub_url, accept="application/vnd.github.v3+json"))
                _fetch_github_dir_recursive(sub_items, files, owner, repo, branch, base_path)
            except Exception:
                continue


def _fetch_github_file(url: str) -> str:
    """Fetch a single file from GitHub. Converts blob URL to raw."""
    # github.com/{owner}/{repo}/blob/{branch}/{path} → raw.githubusercontent.com
    match = re.match(
        r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)', url
    )
    if match:
        owner, repo, branch, path = match.groups()
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        return _http_get(raw_url)
    raise ValueError(f"Cannot parse GitHub file URL: {url}")


def _write_skill_files(s3, skill_id: str, files: dict[str, str]) -> list[str]:
    """Write multiple files to S3 under skills/{skill_id}/. Returns file list."""
    written = []
    for path, content in files.items():
        safe_path = re.sub(r'[^\w./\-]', '_', path)
        if ".." in safe_path:
            continue
        key = f"skills/{skill_id}/{safe_path}"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown" if safe_path.endswith(".md") else "text/plain",
        )
        if safe_path != "SKILL.md":
            written.append(safe_path)
    return sorted(written)


def _update_skill_index(s3_client, new_entry: dict):
    """Read index.json, append/update entry, write back."""
    index = []
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        index = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        pass
    index = [e for e in index if e.get("id") != new_entry["id"]]
    index.append(new_entry)
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key="skills/index.json",
        Body=json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


@tool
def import_skill(
    content: str = "",
    url: str = "",
    name: str = "",
    description: str = "",
) -> str:
    """Import a skill from a URL or raw markdown content.

    Supports multiple URL sources:
    - ClawHub: clawhub.ai/{author}/{slug} — downloads full skill package (zip)
    - GitHub directory: github.com/{owner}/{repo}/tree/{branch}/{path} — fetches all files
    - GitHub file: github.com/{owner}/{repo}/blob/{branch}/{path} — fetches single file
    - Raw URL: any URL — fetches content directly

    If the content already has AgentSkills.io YAML frontmatter, it is used as-is.
    If it's plain markdown, frontmatter is auto-generated using name and description.

    Args:
        content: Raw markdown content. Either content or url must be provided.
        url: URL to import from. Either content or url must be provided.
        name: Skill name override (used if content has no frontmatter).
        description: Skill description override.

    Returns:
        JSON with skill_id, name, files_count, and status.
    """
    files: dict[str, str] = {}
    source_type = "content"

    # --- URL handling ---
    if url and not content:
        url = url.strip()
        try:
            if "clawhub.ai/" in url or "claw-hub.net/" in url:
                source_type = "clawhub"
                files = _fetch_clawhub(url)

            elif "github.com/" in url and "/tree/" in url:
                source_type = "github-dir"
                files = _fetch_github_dir(url)

            elif "github.com/" in url and "/blob/" in url:
                source_type = "github-file"
                content = _fetch_github_file(url)

            else:
                source_type = "url"
                content = _http_get(url)

        except Exception as e:
            return json.dumps({"error": f"Failed to fetch from {source_type}: {e}"})

    # --- Multi-file import (ClawHub / GitHub dir) ---
    if files:
        skill_md = files.get("SKILL.md", "")
        if not skill_md:
            # Try to find SKILL.md in subdirectory
            for path, c in files.items():
                if path.endswith("/SKILL.md") or path == "SKILL.md":
                    skill_md = c
                    break

        if not skill_md:
            return json.dumps({"error": "No SKILL.md found in the imported files. A valid skill must contain SKILL.md."})

        meta = _parse_frontmatter(skill_md)
        skill_name = name or (meta.get("name") if meta else None) or "imported-skill"
        skill_desc = description or (meta.get("description") if meta else None) or ""

        # Ensure SKILL.md has frontmatter
        if not meta:
            skill_md = _wrap_with_frontmatter(skill_md, skill_name, skill_desc, source=source_type)
            files["SKILL.md"] = skill_md

        skill_id = str(uuid.uuid4())[:8]
        s3 = boto3.client("s3", region_name=REGION)

        extra_files = _write_skill_files(s3, skill_id, files)

        _update_skill_index(s3, {
            "id": skill_id,
            "name": skill_name,
            "description": skill_desc,
            "files": extra_files,
        })

        return json.dumps({
            "skill_id": skill_id,
            "name": skill_name,
            "description": skill_desc,
            "source": source_type,
            "files_count": len(files),
            "files": sorted(files.keys()),
            "status": "imported",
        }, indent=2)

    # --- Single-file import (content or single URL) ---
    if not content or not content.strip():
        return json.dumps({"error": "No content provided. Pass content or url."})

    meta = _parse_frontmatter(content)
    if meta:
        skill_name = name or meta.get("name", "imported-skill")
        skill_desc = description or meta.get("description", "")
        skill_md = content
    else:
        if not name:
            return json.dumps({
                "error": "Content has no YAML frontmatter. Provide name and description to auto-generate it.",
                "hint": "Set name (kebab-case) and description for the skill.",
            })
        skill_name = name
        skill_desc = description or f"Imported skill: {name}"
        skill_md = _wrap_with_frontmatter(content, skill_name, skill_desc, source=source_type)

    skill_id = str(uuid.uuid4())[:8]
    s3 = boto3.client("s3", region_name=REGION)

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"skills/{skill_id}/SKILL.md",
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    _update_skill_index(s3, {
        "id": skill_id,
        "name": skill_name,
        "description": skill_desc,
    })

    return json.dumps({
        "skill_id": skill_id,
        "name": skill_name,
        "description": skill_desc,
        "source": source_type,
        "files_count": 1,
        "files": ["SKILL.md"],
        "had_frontmatter": meta is not None,
        "status": "imported",
    }, indent=2)
