"""validate_skill — Static check of a skill's SKILL.md + its ``requires:`` block.

Goals:
- Parse the YAML frontmatter and confirm required fields (``name``, ``description``).
- If ``requires:`` is present, check each item against what's actually reachable:
  * ``type: asset``  — path exists in the skill's S3 tree
  * ``type: package`` — present in the CI's pre-installed set (best-effort allow-list)
  * ``type: runtime`` — compatible with AgentCore Code Interpreter

The tool does NOT spin up a Code Interpreter session — that would be expensive
and slow for a static validate. For runtime package availability probing (i.e.
"can we actually import python-pptx right now?"), the sub-agent's
``check_capabilities`` tool does the live probe.
"""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


# Packages known to be pre-installed in the AgentCore Code Interpreter base
# image (observed against ci-prod as of 2026-04). This is not exhaustive —
# a package not in this set is reported as "uncertain", not "missing".
_CI_PREINSTALLED = {
    "numpy", "pandas", "matplotlib", "seaborn", "plotly",
    "scipy", "scikit-learn", "statsmodels",
    "requests", "httpx", "urllib3",
    "beautifulsoup4", "bs4", "lxml",
    "pillow", "pil",
    "openpyxl", "xlrd", "xlsxwriter",
    "pypdf", "pypdf2", "pdfminer.six", "pdfplumber",
    "pyyaml", "yaml",
    "boto3", "botocore",
    "python-pptx", "pptx",
    "reportlab",
    "sympy",
    "bedrock-agentcore",
}

_SUPPORTED_RUNTIMES = {"code_interpreter"}
_SUPPORTED_RUNTIME_LANGUAGES = {"python", "javascript", "typescript", "shell"}


def _parse_frontmatter(content: str) -> dict | None:
    """Same parser as import_skill — yaml.safe_load the ``---`` block.

    Kept local to avoid a cross-tool import; the two files stay small.
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
    return meta if isinstance(meta, dict) and meta.get("name") else None


def _find_skill_id(s3, name: str) -> str | None:
    """Look up a skill by name via skills/index.json."""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        index = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        return None
    for entry in index:
        if entry.get("name") == name and not entry.get("deleted"):
            return entry.get("id")
    return None


def _list_skill_files(s3, skill_id: str) -> set[str]:
    """Return the set of relative file paths inside skills/{id}/."""
    prefix = f"skills/{skill_id}/"
    out: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(prefix):]
            if rel:
                out.add(rel)
    return out


def _validate_requirement(req: dict, skill_files: set[str]) -> dict:
    """Check one ``requires:`` entry. Returns {ok, note, suggestion}."""
    if not isinstance(req, dict):
        return {"ok": False, "note": f"requirement must be a mapping, got {type(req).__name__}"}

    rtype = (req.get("type") or "").strip().lower()
    if not rtype:
        return {"ok": False, "note": "requirement missing 'type' field"}

    if rtype == "asset":
        path = (req.get("path") or "").strip().lstrip("/")
        if not path:
            return {"ok": False, "note": "asset requirement missing 'path'"}
        if path.endswith("/"):
            hit = any(f.startswith(path) for f in skill_files)
            return {
                "ok": hit,
                "note": f"directory '{path}' present" if hit else f"directory '{path}' not found under skill",
            }
        return {
            "ok": path in skill_files,
            "note": f"file '{path}' present" if path in skill_files else f"file '{path}' not found under skill",
        }

    if rtype == "package":
        pkg = (req.get("name") or "").strip().lower()
        if not pkg:
            return {"ok": False, "note": "package requirement missing 'name'"}
        if pkg in _CI_PREINSTALLED:
            return {"ok": True, "note": f"'{pkg}' in CI pre-installed allow-list"}
        return {
            "ok": False,
            "note": (
                f"'{pkg}' not in known CI pre-installed set — may still work "
                f"at runtime but the sub-agent will need to install it."
            ),
            "suggestion": f"consider calling run_command('pip install {pkg}') before use",
        }

    if rtype == "runtime":
        kind = (req.get("kind") or "").strip().lower()
        lang = (req.get("language") or "python").strip().lower()
        if kind not in _SUPPORTED_RUNTIMES:
            return {"ok": False, "note": f"unsupported runtime kind '{kind}'; supported: {sorted(_SUPPORTED_RUNTIMES)}"}
        if lang not in _SUPPORTED_RUNTIME_LANGUAGES:
            return {"ok": False, "note": f"unsupported runtime language '{lang}'; supported: {sorted(_SUPPORTED_RUNTIME_LANGUAGES)}"}
        return {"ok": True, "note": f"runtime '{kind}/{lang}' is supported"}

    return {"ok": False, "note": f"unknown requirement type '{rtype}' (expected asset|package|runtime)"}


@tool
def validate_skill(skill_name: str) -> str:
    """Statically validate a skill's SKILL.md + ``requires:`` block.

    Use this before advertising a skill as ready for end-users, or after
    importing a skill from an external source. The check does not execute
    any skill code — it only reads S3.

    Args:
        skill_name: The skill to validate (e.g. "ppt-generator").

    Returns:
        JSON report with keys:
          - ``ok`` (bool) — overall pass/fail
          - ``skill_id`` (str)
          - ``frontmatter`` — parsed YAML mapping (minus body)
          - ``missing_fields`` — list of required fields that are absent
          - ``requirements`` — list of {type, detail, ok, note, suggestion?}
          - ``file_count`` — number of files in the skill's S3 tree
    """
    if not skill_name or not isinstance(skill_name, str):
        return json.dumps({"ok": False, "error": "skill_name is required"})

    s3 = boto3.client("s3", region_name=REGION)
    skill_id = _find_skill_id(s3, skill_name)
    if not skill_id:
        return json.dumps({"ok": False, "error": f"skill '{skill_name}' not found in index.json"})

    try:
        md_obj = s3.get_object(Bucket=S3_BUCKET, Key=f"skills/{skill_id}/SKILL.md")
        content = md_obj["Body"].read().decode("utf-8")
    except Exception as e:
        return json.dumps({"ok": False, "skill_id": skill_id, "error": f"failed to read SKILL.md: {e}"})

    meta = _parse_frontmatter(content)
    if meta is None:
        return json.dumps({
            "ok": False,
            "skill_id": skill_id,
            "error": "SKILL.md has no valid YAML frontmatter (expected --- block with 'name' field)",
        })

    missing = [f for f in ("name", "description") if not meta.get(f)]

    skill_files = _list_skill_files(s3, skill_id)
    requirements_out: list[dict] = []
    requires = meta.get("requires") or []
    if not isinstance(requires, list):
        requirements_out.append({
            "ok": False,
            "type": "<malformed>",
            "note": f"'requires' must be a list, got {type(requires).__name__}",
        })
    else:
        for req in requires:
            check = _validate_requirement(req, skill_files)
            entry = {
                "type": (req.get("type") if isinstance(req, dict) else None),
                "detail": req,
            }
            entry.update(check)
            requirements_out.append(entry)

    overall_ok = (not missing) and all(r.get("ok") for r in requirements_out)

    report = {
        "ok": overall_ok,
        "skill_name": skill_name,
        "skill_id": skill_id,
        "file_count": len(skill_files),
        "missing_fields": missing,
        "frontmatter": {k: v for k, v in meta.items() if k != "body"},
        "requirements": requirements_out,
    }
    return json.dumps(report, indent=2, ensure_ascii=False)
