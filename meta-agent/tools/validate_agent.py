"""validate_agent — Pre-deploy validation of agent configuration."""

import ast
import json
import re

from config import MODEL_ID
from strands import Agent, tool
from strands.models import BedrockModel


def _extract_tool_blocks(source: str) -> str:
    """Extract only @tool decorated function blocks from source code.

    Skips template boilerplate like _stream_with_tools, @app.entrypoint, etc.
    Returns concatenated @tool blocks, or empty string if none found.
    """
    lines = source.split("\n")
    blocks = []
    in_tool = False
    current: list = []
    imports: list = []
    found_first = False

    for line in lines:
        trimmed = line.lstrip()
        if trimmed == "@tool":
            if in_tool and current:
                blocks.append("\n".join(current))
            in_tool = True
            found_first = True
            current = [line]
            continue
        if in_tool:
            if trimmed and line[0:1] not in (" ", "\t") and not trimmed.startswith("def ") and not trimmed.startswith("#"):
                blocks.append("\n".join(current))
                in_tool = False
                current = []
                if trimmed.startswith(("async def _", "def _", "@app.")):
                    break
            else:
                current.append(line)
        elif not found_first and trimmed.startswith(("import ", "from ")):
            imports.append(line)

    if in_tool and current:
        blocks.append("\n".join(current))

    if not blocks:
        return ""
    return "\n".join(imports) + "\n\n" + "\n\n".join(blocks) if imports else "\n\n".join(blocks)

# Write-operation patterns that readonly agents should not use
_WRITE_PATTERNS = [
    r'\bput_item\b', r'\bdelete_item\b', r'\bupdate_item\b',
    r'\bput_object\b', r'\bdelete_object\b',
    r'\bcreate_\w+\b', r'\bdelete_\w+\b', r'\bupdate_\w+\b',
    r'\bINSERT\s+INTO\b', r'\bUPDATE\s+\w+\s+SET\b', r'\bDELETE\s+FROM\b',
    r'\bDROP\s+TABLE\b', r'\bCREATE\s+TABLE\b',
    r'\.put\(', r'\.delete\(', r'\.post\(',
    r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
]
_WRITE_RE = re.compile('|'.join(_WRITE_PATTERNS), re.IGNORECASE)

# Libraries NOT available in the sandbox
_UNAVAILABLE_LIBS = {
    'scrapy', 'selenium', 'playwright', 'pandas', 'numpy', 'scipy',
    'Pillow', 'PIL', 'feedparser', 'lxml', 'matplotlib', 'seaborn',
    'sklearn', 'tensorflow', 'torch', 'cv2', 'flask', 'django',
    'fastapi', 'sqlalchemy', 'celery', 'redis',
}


def _get_builtin_tool_names() -> set[str]:
    """Dynamically get all built-in tool names from tools_library registry."""
    try:
        from tools_library import registry
        names = set()
        for mod in registry._ALL_TOOLS:
            for n in mod.TOOL_NAMES.split(","):
                n = n.strip()
                if n:
                    names.add(n)
        return names
    except Exception:
        return set()

def _get_mcp_tool_names(mcp_targets_list: list[str]) -> list[str]:
    """Fetch tool names from S3 manifests for the given MCP targets.

    Reads mcp/target-tools/{target}.json from S3 for each target.
    Falls back to hyphen→underscore conversion if exact key not found.
    Silently skips targets whose manifests are missing.
    """
    if not mcp_targets_list:
        return []
    try:
        import boto3
        from config import REGION, S3_BUCKET
        s3 = boto3.client("s3", region_name=REGION)
    except Exception:
        return []

    names = []
    for target in mcp_targets_list:
        # Try multiple key variants: exact, hyphen→underscore, mcp- prefix
        candidates = [target]
        alt = target.replace("-", "_")
        if alt != target:
            candidates.append(alt)
        candidates.append(f"mcp-{target}")
        candidates.append(f"mcp_{alt}")

        for key_name in candidates:
            try:
                resp = s3.get_object(Bucket=S3_BUCKET, Key=f"mcp/target-tools/{key_name}.json")
                tools = json.loads(resp["Body"].read().decode("utf-8"))
                for t in tools:
                    name = t.get("name", "")
                    if name:
                        names.append(name)
                break  # found manifest, skip alternate key
            except Exception:
                continue
    return names


_PROMPT_REVIEW_SYSTEM = """\
## Role
You are a prompt quality reviewer for AI agent system prompts. Score each dimension 1-5 based on the ACTUAL agent configuration provided (tools, permission tier, description). Find specific, actionable problems — not generic advice.

## Scoring Dimensions (1-5 each)

### 1. structure
Does the prompt have clear sections with markdown headers (##)?
- 5: Has Role, Capabilities, Constraints, Tool Usage, Output Format, Safety sections
- 3: Some sections present but missing key ones
- 1: Wall of text with no structure

### 2. tool_prompt_sync
Is EVERY tool mentioned with SPECIFIC usage guidance?
- 5: Every tool has "When user asks X, use tool_name to Y" guidance
- 3: Some tools have guidance, others are just listed by name
- 1: Tools not mentioned, OR section title exists but content is empty/generic
- N/A (score 5): Agent has no tools

CRITICAL: A "## Tool Usage" header with no per-tool guidance scores 1-2, NOT 5.
WRONG (score 1): "## Tool Usage\\n(empty)"
WRONG (score 2): "## Tool Usage\\nUse tools when needed."
CORRECT (score 5): "## Tool Usage\\n- query_metrics: Use when user asks about performance data.\\n- list_alarms: Use when user asks about alarm status."

### 3. constraint_strength
Are critical constraints enforced with strong language and consequences?
- 5: Uses "NEVER"/"STRICTLY PROHIBITED", explains WHY, has recovery gates
- 3: Has constraints but weak language ("try to avoid", "prefer not to")
- 1: No constraints or only vague guidelines

WRONG: "Try to avoid modifying data"
CORRECT: "NEVER execute write operations. You are read-only. If user requests a write, explain you can only read and suggest the appropriate admin tool."

### 4. anti_patterns
Does the prompt show what NOT to do with concrete WRONG/RIGHT examples?
- 5: Has WRONG/RIGHT examples for common mistakes
- 3: Lists don'ts but without examples
- 1: No anti-patterns documented

### 5. rationalization_preemption
Does the prompt predict and counter the agent's likely excuses for skipping tools?
- 5: Lists 3+ specific excuses with counters ("You may think X — do Y instead")
- 3: Has general "always use tools" guidance
- 1: No preemption — agent will skip tools freely
- N/A (score 5): Agent has no tools

### 6. output_format
Is the expected response format clearly defined?
- 5: Shows exact format with examples (tables, prose, code blocks)
- 3: Mentions format preferences but no examples
- 1: No format guidance

### 7. language_consistency
Is the prompt in the same language as the agent's description/welcome_message?
- 5: Fully consistent language throughout
- 3: Mixed languages
- 1: Completely mismatched

### 8. safety
Evaluate based on the ACTUAL tools and permission tier — not generic rules.
- If agent has NO tools or only read-only tools (read, list, get, describe, query, chart, analyze): score 5 with basic constraints like "do not fabricate data". Do NOT require write-operation prohibitions.
- If agent has WRITE-capable tools (create, update, delete, put, insert, post) AND permission is readonly/basic: score 5 requires explicit prohibition of write operations with specific operations listed.
- If agent has WRITE-capable tools AND permission is data-access: score 5 requires scoped write permissions (which resources/operations are allowed).
- Do NOT penalize for missing SQL prohibitions if agent has no SQL tools. Do NOT penalize for missing S3 write restrictions if agent has no S3 write tools.

## Constraints
- Return ONLY a JSON object. No markdown fences, no explanation before or after.
- Be STRICT. Empty sections with only a title score 1, not 5.
- Do NOT be lenient. Recognize your excuses:
  - "The prompt is short but covers the basics" — short prompts almost always lack tool guidance and constraints. Score accordingly.
  - "The intent is clear enough" — if a tool is not mentioned BY NAME with usage guidance, score tool_prompt_sync low.
  - "The constraints are implied" — implicit constraints don't work. The agent needs explicit "NEVER" statements.
- Issues must be SPECIFIC to this agent's actual tools and purpose: "Tool 'list_alarms' has no usage guidance" not "improve tool section".
- Issues must be ACTIONABLE PROBLEMS that need fixing. Do NOT include positive observations, compliments, or things that are already good. If a dimension scores 4-5, do NOT add an issue for it.
- Max 5 issues, most important first.
- Respond in the same language as the system prompt being reviewed.

## Output Format
{"scores": {"structure": N, "tool_prompt_sync": N, "constraint_strength": N, "anti_patterns": N, "rationalization_preemption": N, "output_format": N, "language_consistency": N, "safety": N}, "overall": N.N, "issues": ["specific fix 1", "specific fix 2"]}
"""


def _review_prompt_quality(system_prompt: str, tool_names_list: list[str], permission_tier: str,
                           description: str = "", welcome_message: str = "") -> dict | None:
    """Use an Agent to review prompt quality. Returns scores dict or None on failure."""
    reviewer = Agent(
        model=BedrockModel(model_id=MODEL_ID),
        system_prompt=_PROMPT_REVIEW_SYSTEM,
    )

    review_input = f"""Review this agent's system prompt:

```
{system_prompt}
```

Agent context:
- Tools: [{', '.join(tool_names_list) if tool_names_list else 'none'}]
- Permission tier: {permission_tier or 'unknown'}
- Description: {description or '(empty)'}
- Welcome message: {welcome_message or '(empty)'}

Return the JSON scores."""

    result = reviewer(review_input)
    text = str(result)

    # Extract JSON — find the outermost { } containing "scores"
    start = text.find('{"scores"')
    if start == -1:
        start = text.find('{')
    if start == -1:
        return None

    depth = 0
    end = -1
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return None

    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


@tool
def validate_agent(
    agent_name: str = "",
    system_prompt: str = "",
    tool_definitions: str = "",
    tool_names: str = "",
    description: str = "",
    welcome_message: str = "",
    permission_tier: str = "",
    staging_key: str = "",
) -> str:
    """Validate an agent's configuration before deployment.

    Checks:
    1. Required fields (name, description, system_prompt)
    2. Python syntax of tool_definitions (compile check)
    3. tool_names matches actual @tool function names in tool_definitions
    4. system_prompt references tools that exist
    5. Unavailable library imports
    6. Readonly permission tier vs write operations in tool code
    7. Security patterns (os.system, subprocess)

    Args:
        agent_name: The agent name.
        system_prompt: The system prompt text.
        tool_definitions: Python code with @tool decorated functions.
        tool_names: Comma-separated tool names.
        description: Agent description.
        welcome_message: Welcome message.
        permission_tier: Permission tier (basic/readonly/data-access).
        staging_key: S3 key to a JSON file containing all parameters.

    Returns:
        JSON with validation results: {valid: bool, errors: [...], warnings: [...]}
    """
    # If staging_key provided, read params from S3
    mcp_targets_raw = []
    if staging_key:
        try:
            import boto3
            from config import REGION, S3_BUCKET
            s3 = boto3.client("s3", region_name=REGION)
            obj = s3.get_object(Bucket=S3_BUCKET, Key=staging_key)
            staged = json.loads(obj["Body"].read().decode("utf-8"))
            agent_name = staged.get("name", agent_name) or agent_name
            system_prompt = staged.get("system_prompt", system_prompt) or system_prompt
            tool_definitions = staged.get("tool_definitions", tool_definitions) or tool_definitions
            tool_names = staged.get("tool_names", tool_names) or tool_names
            description = staged.get("description", description) or description
            welcome_message = staged.get("welcome_message", welcome_message) or welcome_message
            permission_tier = staged.get("permission_tier", permission_tier) or permission_tier
            mcp_targets_raw = staged.get("mcp_targets", [])
        except Exception as e:
            return json.dumps({"valid": False, "errors": [f"Failed to read staging config: {e}"], "warnings": []})

    errors = []
    warnings = []

    # 1. Required fields
    if not agent_name or not agent_name.strip():
        errors.append("Agent name is required.")
    elif not re.match(r'^[A-Za-z0-9]+$', agent_name):
        errors.append(f"Agent name '{agent_name}' must be alphanumeric only (no hyphens, underscores, or spaces).")
    if not description or not description.strip():
        warnings.append("Description is empty. Consider adding one for discoverability.")
    if not system_prompt or not system_prompt.strip():
        errors.append("System prompt is required.")

    # 2. Python syntax check for tool_definitions
    defined_funcs = []
    if tool_definitions and tool_definitions.strip():
        defined_funcs = re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', tool_definitions)

        try:
            ast.parse(tool_definitions)
        except SyntaxError as e:
            errors.append(f"Python syntax error in tool_definitions: {e.msg} (line {e.lineno})")

        # 2b. Dry-run: exec tool code and verify @tool functions are callable
        if not any("syntax error" in e.lower() for e in errors):
            try:
                # Extract only @tool blocks for dry-run (skip template boilerplate)
                tool_code_for_exec = _extract_tool_blocks(tool_definitions) or tool_definitions
                # Create a mock @tool decorator that just returns the function
                exec_globals = {"__builtins__": __builtins__}
                exec_globals["tool"] = lambda f: f  # mock @tool
                exec(tool_code_for_exec, exec_globals)
                # Verify each @tool function exists and is callable
                for fname in defined_funcs:
                    fn = exec_globals.get(fname)
                    if fn is None:
                        warnings.append(f"Dry-run: @tool function '{fname}' not found after exec.")
                    elif not callable(fn):
                        warnings.append(f"Dry-run: '{fname}' is not callable.")
                    else:
                        # Try calling with introspected default args to catch obvious type errors
                        import inspect
                        sig = inspect.signature(fn)
                        test_args = {}
                        for pname, param in sig.parameters.items():
                            if param.default is not inspect.Parameter.empty:
                                test_args[pname] = param.default
                            elif param.annotation == str or "str" in str(param.annotation):
                                test_args[pname] = ""
                            elif param.annotation == int or "int" in str(param.annotation):
                                test_args[pname] = 0
                            elif param.annotation == list or "list" in str(param.annotation):
                                test_args[pname] = []
                            elif param.annotation == dict or "dict" in str(param.annotation):
                                test_args[pname] = {}
                            elif param.annotation == bool or "bool" in str(param.annotation):
                                test_args[pname] = False
                            elif param.annotation == float or "float" in str(param.annotation):
                                test_args[pname] = 0.0
                            else:
                                test_args[pname] = ""
                        try:
                            fn(**test_args)
                        except Exception as call_err:
                            err_type = type(call_err).__name__
                            warnings.append(f"Dry-run: '{fname}' raised {err_type} with default args: {call_err}")
            except Exception as exec_err:
                warnings.append(f"Dry-run exec failed: {type(exec_err).__name__}: {exec_err}")

        # 5. Check for unavailable library imports
        imports = re.findall(r'(?:from\s+(\w+)|import\s+(\w+))', tool_definitions)
        for imp in imports:
            lib = imp[0] or imp[1]
            if lib in _UNAVAILABLE_LIBS:
                errors.append(f"Library '{lib}' is not available in the sandbox. Use MCP Gateway or a different approach.")

        # 6. (Removed) Previously checked readonly tier vs write operations.
        # All agents now use a unified role with sufficient permissions.

        # 7. Security patterns
        if "import os" in tool_definitions and ("os.system" in tool_definitions or "subprocess" in tool_definitions):
            warnings.append("Tool code uses os.system or subprocess — ensure this is intentional and safe.")

    # 3. tool_names vs actual @tool functions (exclude built-in tools and MCP tools)
    declared_names = [t.strip() for t in tool_names.split(",") if t.strip()] if tool_names else []
    builtin_names = _get_builtin_tool_names()

    # Resolve MCP tool names (for steps 3 and 8)
    mcp_target_names = (
        [t.strip() for t in mcp_targets_raw.split(",") if t.strip()]
        if isinstance(mcp_targets_raw, str) else list(mcp_targets_raw)
    )
    mcp_tool_names = _get_mcp_tool_names(mcp_target_names)
    mcp_tool_names_set = set(mcp_tool_names)

    # 2c. MCP target IAM permission check
    # For each mcp_target in the proposal, verify the workspace role has
    # the required IAM permissions declared in mcp-registry.yaml.
    if mcp_target_names:
        try:
            from tools._scope import current_workspace
            from tools.list_mcp_servers import (
                _check_iam_permissions,
                _get_workspace_role_arn,
                _load_registry_iam_policies,
            )

            iam_policies = _load_registry_iam_policies()
            ws_id = current_workspace()
            workspace_role_arn = _get_workspace_role_arn(ws_id) if ws_id else None

            for target in mcp_target_names:
                iam_policy = iam_policies.get(target)
                if iam_policy is None:
                    # No IAM policy declared — platform tool, always allowed
                    continue

                if not workspace_role_arn:
                    # Target requires IAM permissions but workspace has no custom role
                    actions = []
                    for stmt in iam_policy.get("Statement", []):
                        a = stmt.get("Action", [])
                        actions.extend(a if isinstance(a, list) else [a])
                    errors.append(
                        f"MCP target '{target}' requires IAM permissions "
                        f"({', '.join(actions)}) but this workspace has no "
                        f"custom IAM role. Create one in Settings → Workspace → "
                        f"IAM Role, then grant permissions for '{target}'."
                    )
                    continue

                # Workspace has a role — check if it has the required permissions
                result = _check_iam_permissions(workspace_role_arn, iam_policy)
                if not result["granted"]:
                    missing = result.get("missing_actions", [])
                    # Extract role name from ARN for CLI command
                    role_name = workspace_role_arn.rsplit("/", 1)[-1] if "/" in workspace_role_arn else workspace_role_arn
                    policy_json = json.dumps(iam_policy, separators=(",", ":"))
                    cli_cmd = (
                        f"aws iam put-role-policy "
                        f"--role-name {role_name} "
                        f"--policy-name MCP-{target} "
                        f"--policy-document '{policy_json}'"
                    )
                    errors.append(
                        f"MCP target '{target}' requires IAM permissions that "
                        f"the workspace role is missing: [{', '.join(missing)}]. "
                        f"Grant via Settings → IAM Permissions → one-click authorize, "
                        f"or run:\n{cli_cmd}"
                    )
        except Exception as e:
            warnings.append(f"MCP IAM permission check skipped: {e}")

    if defined_funcs and declared_names:
        defined_set = set(defined_funcs)
        declared_set = set(declared_names)

        # Built-in and MCP tools won't be in tool_definitions — that's expected
        missing_in_code = (declared_set - defined_set) - builtin_names - mcp_tool_names_set
        missing_in_names = defined_set - declared_set

        if missing_in_code:
            errors.append(f"tool_names declares [{', '.join(sorted(missing_in_code))}] but no matching @tool function found in code.")
        if missing_in_names:
            warnings.append(f"@tool functions [{', '.join(sorted(missing_in_names))}] exist in code but not listed in tool_names. They will be ignored at runtime.")
    elif defined_funcs and not declared_names:
        warnings.append(f"tool_definitions has {len(defined_funcs)} @tool functions but tool_names is empty. Tools won't be registered.")
    elif declared_names and not defined_funcs:
        # All declared names are built-in — no warning needed
        custom_names = set(declared_names) - builtin_names - mcp_tool_names_set
        if custom_names:
            warnings.append(f"tool_names declares [{', '.join(sorted(custom_names))}] but tool_definitions has no matching @tool functions.")

    # 4. system_prompt ↔ tool consistency
    if system_prompt and defined_funcs:
        prompt_lower = system_prompt.lower()
        for func_name in defined_funcs:
            readable_name = func_name.replace("_", " ")
            if func_name not in prompt_lower and readable_name not in prompt_lower:
                warnings.append(f"Tool '{func_name}' is not mentioned in system_prompt. The agent may not know when to use it.")

    # 4b. Ghost-tool detection — names the prompt references in backticks that
    # don't exist in any known tool surface. This is the check that would have
    # caught the `audit_services` hallucination in AWSCloudOpsAssistant.
    # Only flag snake_case identifiers in backticks to avoid false positives
    # on AWS service names, English phrases, or MCP target/category names.
    if system_prompt:
        # Extract `snake_case_ident` tokens — same shape as real tool names
        backtick_tokens = set(re.findall(r'`([a-z][a-z0-9_]*[a-z0-9])`', system_prompt))
        # Known tool surfaces the agent will actually have at runtime
        known = set(defined_funcs or []) | set(declared_names) | set(builtin_names) | mcp_tool_names_set
        # Skill-provided @tool functions (if staging_key tells us about skills)
        if staging_key:
            try:
                for skill_entry in staged.get("skills", []) or []:
                    sid = skill_entry.get("id", "")
                    if not sid:
                        continue
                    # Re-use S3 client from step 2 if present; cheap to rebuild if not
                    s3_scan = boto3.client("s3", region_name=REGION)
                    prefix = f"agents/{staged.get('agent_id', agent_name)}/skills/{sid}/scripts/"
                    try:
                        resp = s3_scan.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
                        for obj in resp.get("Contents", []):
                            if obj["Key"].endswith(".py"):
                                code = s3_scan.get_object(Bucket=S3_BUCKET, Key=obj["Key"])["Body"].read().decode("utf-8", errors="ignore")
                                known.update(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', code))
                    except Exception:
                        pass
            except Exception:
                pass
        # Runtime builtins that every agent gets (see agent_template_v2.py:275)
        known.update({
            "load_skill", "run_command", "upload_to_s3", "read_document",
            "browser_use", "run_skill_script", "check_capabilities",
        })
        # Common Python/English terms that shouldn't count even in backticks
        allowlist = {
            "true", "false", "none", "null", "json", "str", "int", "bool",
            "list", "dict", "yes", "no",
        }
        ghost = {t for t in backtick_tokens if t not in known and t not in allowlist and "_" in t}
        if ghost:
            errors.append(
                f"system_prompt references tool name(s) {sorted(ghost)} in backticks but "
                f"they aren't in tool_names, tool_definitions, attached skills, or the MCP "
                f"target manifests. Either remove the reference, fix the name (call "
                f"list_mcp_target_tools to see real names), or add the tool."
            )

    # Check for overly long system_prompt
    if system_prompt and len(system_prompt) > 10000:
        warnings.append(f"System prompt is very long ({len(system_prompt)} chars). Consider trimming for better performance.")

    # Skill conflict detection
    skills_config = staged.get("skills", []) if staging_key else []
    if skills_config and staging_key:
        try:
            s3_val = boto3.client("s3", region_name=REGION)
            agent_id_for_path = staged.get("agent_id", agent_name)

            skill_tool_funcs = {}  # skill_name -> [func_names]
            skill_file_names = {}  # skill_name -> [filenames]

            for skill_entry in skills_config:
                skill_id = skill_entry.get("id", "")
                skill_name = skill_entry.get("name", skill_id)
                skill_prefix = f"agents/{agent_id_for_path}/skills/{skill_id}/scripts/"

                try:
                    resp = s3_val.list_objects_v2(Bucket=S3_BUCKET, Prefix=skill_prefix)
                    funcs = []
                    fnames = []
                    for obj in resp.get("Contents", []):
                        key = obj["Key"]
                        filename = key.split("/")[-1]
                        if not filename:
                            continue
                        fnames.append(filename)
                        if filename.endswith(".py"):
                            try:
                                content = s3_val.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8")
                                tool_funcs = re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', content)
                                funcs.extend(tool_funcs)
                            except Exception as e:
                                warnings.append(f"Could not check skill {skill_name} for conflicts: {e}")
                    skill_tool_funcs[skill_name] = funcs
                    skill_file_names[skill_name] = fnames
                except Exception:
                    pass

            # File name collisions across skills
            all_files = {}
            for sname, fnames in skill_file_names.items():
                for fname in fnames:
                    all_files.setdefault(fname, []).append(sname)
            for fname, snames in all_files.items():
                if len(snames) > 1:
                    errors.append(f"Skill script file name collision: '{fname}' exists in skills [{', '.join(snames)}]")

            # @tool function name collisions across skills
            all_funcs = {}
            for sname, funcs in skill_tool_funcs.items():
                for func in funcs:
                    all_funcs.setdefault(func, []).append(sname)
            for func, snames in all_funcs.items():
                if len(snames) > 1:
                    errors.append(f"Skill @tool function name collision: '{func}' defined in skills [{', '.join(snames)}]")

            # Skill @tool vs agent's own tool_definitions
            agent_defined = set(defined_funcs) if defined_funcs else set()
            for sname, funcs in skill_tool_funcs.items():
                for func in funcs:
                    if func in agent_defined:
                        errors.append(f"Skill @tool function '{func}' in skill '{sname}' conflicts with agent's own tool_definitions")
        except Exception as e:
            warnings.append(f"Skill conflict detection skipped: {e}")

    # 8. LLM-based prompt quality review (covers structure, tool sync, constraints, safety, etc.)
    prompt_review = None
    if system_prompt and system_prompt.strip() and len(errors) == 0:
        tool_names_list = list(declared_names or defined_funcs or [])
        if mcp_tool_names:
            tool_names_list.extend(mcp_tool_names)
        try:
            prompt_review = _review_prompt_quality(
                system_prompt, tool_names_list, permission_tier,
                description=description, welcome_message=welcome_message,
            )
        except Exception as e:
            warnings.append(f"Prompt quality review skipped: {e}")
        if prompt_review and "scores" in prompt_review:
            prompt_review["scores"]
            overall = prompt_review.get("overall", 0)
            issues = prompt_review.get("issues", [])

            if overall < 3:
                warnings.append(f"Prompt quality score: {overall}/5 — consider optimizing with Auto-fix or AI assistant.")
            # Only show issues when overall score is low — high scores mean the prompt is good
            if overall < 4:
                for issue in issues[:5]:
                    if isinstance(issue, str) and issue.strip():
                        warnings.append(f"Prompt review: {issue}")

    valid = len(errors) == 0
    result = {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "summary": f"{'PASS' if valid else 'FAIL'}: {len(errors)} error(s), {len(warnings)} warning(s)",
    }
    if prompt_review and "scores" in prompt_review:
        result["prompt_scores"] = prompt_review["scores"]
        result["prompt_overall"] = prompt_review.get("overall", 0)
    return json.dumps(result, indent=2, ensure_ascii=False)
