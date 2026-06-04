#!/usr/bin/env bash
# Warn-only, agent-agnostic check: did a code change make CLAUDE.md stale?
# Runs in lefthook pre-push. Complements the mechanical scripts/check-invariants.sh
# (which cannot judge semantic drift). NEVER blocks a push.
#
# Detects whichever Agent CLI is available (claude/codex/gemini/kiro-cli),
# constructs a prompt with the code diff and CLAUDE.md, and parses the JSON
# response to flag stale documentation.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_TIMEOUT="${DOC_CHECK_AGENT_TIMEOUT:-90}"
DIFF_CHAR_CAP=15000

skip() { echo "SKIP: $1"; exit 0; }

# 1. Determine the push diff range.
range=""
if [ ! -t 0 ]; then
  while read -r _local_ref local_sha _remote_ref remote_sha; do
    [ -z "${local_sha:-}" ] && continue
    case "$local_sha" in *[!0]*) : ;; *) continue ;; esac
    if [ -n "${remote_sha:-}" ] && [ "$remote_sha" != "0000000000000000000000000000000000000000" ]; then
      range="$remote_sha..$local_sha"
    else
      range="$local_sha"
    fi
    break
  done || true
fi
# Fallback (lefthook doesn't forward stdin): find push target or merge-base
if [ -z "$range" ]; then
  base=""
  for ref in '@{push}' '@{upstream}'; do
    cand="$(git rev-parse --verify --quiet "${ref}^{commit}" 2>/dev/null || true)"
    if [ -n "$cand" ]; then base="$cand"; break; fi
  done
  if [ -n "$base" ]; then
    range="$base..HEAD"
  else
    mb="$(git merge-base origin/main HEAD 2>/dev/null || true)"
    if [ -n "$mb" ]; then range="$mb..HEAD"; else range="HEAD"; fi
  fi
fi

# 2. Heuristic gate: only proceed if diff touches doc-relevant code.
if [ "$range" = "HEAD" ] || ! echo "$range" | grep -q '\.\.'; then
  changed="$(git show --name-only --pretty=format: "$range" 2>/dev/null | sort -u || true)"
else
  changed="$(git diff --name-only "$range" 2>/dev/null || true)"
fi

relevant="$(echo "$changed" \
  | grep -E '^(meta-agent|lambda|infra/lib|frontend/src/lib|frontend/src/stores)/.*\.(py|ts|tsx|mjs)$|^scripts/(deploy|build)' \
  | grep -vE '(__tests__|\.test\.|__pycache__)' || true)"
[ -z "$relevant" ] && skip "no doc-relevant code changes in this push"

# 3. Detect an Agent CLI.
AGENT_NAME=""
for c in claude codex gemini kiro-cli cursor-agent llm; do
  if command -v "$c" >/dev/null 2>&1; then AGENT_NAME="$c"; break; fi
done
if [ -z "$AGENT_NAME" ] && [ -n "${DOC_CHECK_AGENT_CMD:-}" ]; then AGENT_NAME="override"; fi
[ -z "$AGENT_NAME" ] && skip "no Agent CLI detected (install claude/codex/gemini/kiro-cli)"

run_agent() {
  local prompt="$1"
  case "$AGENT_NAME" in
    claude)       printf '%s' "$prompt" | timeout "$AGENT_TIMEOUT" claude -p ;;
    codex)        printf '%s' "$prompt" | timeout "$AGENT_TIMEOUT" codex exec - ;;
    gemini)       timeout "$AGENT_TIMEOUT" gemini -p "$prompt" ;;
    kiro-cli)     timeout "$AGENT_TIMEOUT" kiro-cli chat --no-interactive --trust-tools= "$prompt" ;;
    cursor-agent) timeout "$AGENT_TIMEOUT" cursor-agent -p "$prompt" --output-format text ;;
    llm)          printf '%s' "$prompt" | timeout "$AGENT_TIMEOUT" llm ;;
    override)     printf '%s' "$prompt" | timeout "$AGENT_TIMEOUT" sh -c "$DOC_CHECK_AGENT_CMD" ;;
  esac
}

# 4. Build the prompt.
diff_text="$(git diff "$range" -- $relevant 2>/dev/null | head -c "$DIFF_CHAR_CAP" || true)"
[ -z "$diff_text" ] && skip "empty diff for relevant files"
claude_md="$(cat CLAUDE.md 2>/dev/null || true)"

prompt="You are checking whether a code change has made project documentation (CLAUDE.md) factually stale.

Below are (A) a unified diff of changed code files, and (B) the CLAUDE.md that describes the project architecture, security rules, and conventions.

Decide whether any statement in CLAUDE.md is now factually WRONG because of the diff. Focus on:
- File/directory paths that no longer exist or were renamed
- Function/class/tool names that changed
- Numeric values (timeouts, limits, port numbers)
- Security rules that the code now violates
- Architecture descriptions that no longer match

Output ONLY a single line of minified JSON, no prose, no code fences:
{\"consistent\": true} if nothing in CLAUDE.md is contradicted, or
{\"consistent\": false, \"findings\": [\"CLAUDE.md says X but the code now Y\", ...]}

If uncertain, output {\"consistent\": true} (this is advisory; avoid false alarms).

=== (A) CODE DIFF ===
$diff_text

=== (B) CLAUDE.md ===
$claude_md"

# 5. Invoke.
echo "doc-consistency check: asking $AGENT_NAME (up to ${AGENT_TIMEOUT}s, advisory)..." >&2
raw="$(run_agent "$prompt" 2>/dev/null | head -c 65536 || true)"
[ -z "$raw" ] && skip "Agent check unavailable (no output, timeout, or error)"

# 6. Parse JSON response.
cleaned="$(printf '%s' "$raw" | sed 's/```[a-zA-Z]*//g; s/```//g' | tr '\n' ' ')"
json="$(printf '%s' "$cleaned" | grep -oE '\{.*\}' | tail -1 || true)"
[ -z "$json" ] && skip "could not find JSON in Agent output"
consistent="$(echo "$json" | jq -r '.consistent' 2>/dev/null || true)"
case "$consistent" in
  true)  echo "OK: CLAUDE.md appears consistent with this change (checked via $AGENT_NAME)"; exit 0 ;;
  false) : ;;
  *)     skip "could not parse Agent output" ;;
esac

# 7. consistent==false → print findings as warnings, DO NOT block.
echo "WARN: CLAUDE.md may be stale vs this change (advisory, not blocking) [via $AGENT_NAME]:" >&2
echo "$json" | jq -r '.findings[]?' 2>/dev/null | while IFS= read -r f; do
  echo "  - $f" >&2
done
echo "  → review CLAUDE.md before merging." >&2
exit 0
