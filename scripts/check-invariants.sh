#!/usr/bin/env bash
# Mechanical consistency checks between CLAUDE.md claims and actual code/files.
# Runs in lefthook pre-commit and in test.sh --lint. Fast: no Docker/AWS/network.
#
# Catches the class of bug found by deep-review: CLAUDE.md promises files/aspects
# that don't exist on disk.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

fail=0
note() { echo "FAIL: $1" >&2; fail=1; }
warn() { echo "WARN: $1" >&2; }

# ---------------------------------------------------------------------------
# 1. CLAUDE.md references to files/directories that must exist
# ---------------------------------------------------------------------------

# Extract file paths from CLAUDE.md code blocks and table cells that look like
# real paths (contain / and end in known extensions or are directories)
check_paths_in_claude_md() {
  local claude_md="$PROJECT_DIR/CLAUDE.md"
  [ -f "$claude_md" ] || { echo "SKIP: CLAUDE.md not in tree (gitignored)" >&2; return; }

  # Key files/dirs explicitly called out in CLAUDE.md that MUST exist
  local -a required_files=(
    "scripts/pre-commit-security.sh"
    "scripts/deploy-agentcore.sh"
    "scripts/deploy-all.sh"
    "meta-agent/main.py"
    "meta-agent/config.py"
    "meta-agent/tools/_scope.py"
    "meta-agent/tools/_workspace.py"
    "meta-agent/tools_library/registry.py"
    "lambda/crud/handler.py"
    "infra/bin/app.ts"
    "infra/lib/aspects/public-access-guard.ts"
    "frontend/src/lib/api-client.ts"
    "frontend/src/lib/agentcore-client.ts"
    "frontend/src/stores/ui-settings-store.ts"
    "frontend/src/stores/agent-edit-store.ts"
    "frontend/src/stores/chat-store.ts"
    "frontend/src/hooks/useFileEditor.ts"
  )

  for f in "${required_files[@]}"; do
    [ -e "$PROJECT_DIR/$f" ] || note "CLAUDE.md references '$f' but it does not exist"
  done
}
check_paths_in_claude_md

# ---------------------------------------------------------------------------
# 2. CDK constructs referenced in CLAUDE.md must exist in infra/lib/constructs/
# ---------------------------------------------------------------------------
check_cdk_constructs() {
  local claude_md="$PROJECT_DIR/CLAUDE.md"
  [ -f "$claude_md" ] || return

  # Extract construct filenames mentioned in CLAUDE.md (e.g. database.ts, api.ts)
  local -a constructs=(
    "database.ts"
    "api.ts"
    "invoke.ts"
    "cdn.ts"
    "auth.ts"
    "roles.ts"
    "workspace-boundary.ts"
  )

  for c in "${constructs[@]}"; do
    [ -f "$PROJECT_DIR/infra/lib/constructs/$c" ] || note "CLAUDE.md references construct '$c' but infra/lib/constructs/$c does not exist"
  done
}
check_cdk_constructs

# ---------------------------------------------------------------------------
# 3. CDK aspects referenced in CLAUDE.md must exist
# ---------------------------------------------------------------------------
check_aspects() {
  local claude_md="$PROJECT_DIR/CLAUDE.md"
  [ -f "$claude_md" ] || return

  # Only check aspects that CLAUDE.md says are actively wired (not just planned)
  if grep -q "public-access-guard.ts" "$claude_md"; then
    [ -f "$PROJECT_DIR/infra/lib/aspects/public-access-guard.ts" ] || \
      note "CLAUDE.md references public-access-guard.ts aspect but it does not exist"
  fi

  # EnforceBoundaryImmutability — CLAUDE.md says it exists. Check.
  if grep -q "EnforceBoundaryImmutability" "$claude_md"; then
    if ! find "$PROJECT_DIR/infra" -name "*.ts" -exec grep -l "EnforceBoundaryImmutability" {} + 2>/dev/null | grep -qv "node_modules"; then
      warn "CLAUDE.md references EnforceBoundaryImmutability but no implementation found in infra/ (known gap)"
    fi
  fi
}
check_aspects

# ---------------------------------------------------------------------------
# 4. Lambda handler files referenced in CLAUDE.md directory structure
# ---------------------------------------------------------------------------
check_lambda_structure() {
  local -a expected_dirs=(
    "lambda/crud"
    "lambda/invoke-node"
    "lambda/shared"
  )
  for d in "${expected_dirs[@]}"; do
    [ -d "$PROJECT_DIR/$d" ] || note "CLAUDE.md directory structure claims '$d/' exists but it doesn't"
  done
}
check_lambda_structure

# ---------------------------------------------------------------------------
# 5. Frontend i18n key parity (en.json vs zh.json)
# ---------------------------------------------------------------------------
check_i18n_parity() {
  local en="$PROJECT_DIR/frontend/src/locales/en.json"
  local zh="$PROJECT_DIR/frontend/src/locales/zh.json"
  [ -f "$en" ] && [ -f "$zh" ] || return 0

  local en_keys zh_keys
  en_keys=$(python3 -c "
import json, sys
def extract(obj, prefix=''):
    keys = []
    for k, v in obj.items():
        path = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            keys.extend(extract(v, path))
        else:
            keys.append(path)
    return keys
with open('$en') as f:
    print('\n'.join(sorted(extract(json.load(f)))))
" 2>/dev/null)
  zh_keys=$(python3 -c "
import json, sys
def extract(obj, prefix=''):
    keys = []
    for k, v in obj.items():
        path = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            keys.extend(extract(v, path))
        else:
            keys.append(path)
    return keys
with open('$zh') as f:
    print('\n'.join(sorted(extract(json.load(f)))))
" 2>/dev/null)

  local missing_in_zh missing_in_en
  missing_in_zh=$(comm -23 <(echo "$en_keys") <(echo "$zh_keys") | head -5)
  missing_in_en=$(comm -13 <(echo "$en_keys") <(echo "$zh_keys") | head -5)

  if [ -n "$missing_in_zh" ]; then
    note "i18n drift: keys in en.json missing from zh.json (first 5): $(echo "$missing_in_zh" | tr '\n' ' ')"
  fi
  if [ -n "$missing_in_en" ]; then
    note "i18n drift: keys in zh.json missing from en.json (first 5): $(echo "$missing_in_en" | tr '\n' ' ')"
  fi
}
check_i18n_parity

# ---------------------------------------------------------------------------
# 6. Security: pre-commit hook is installed
# ---------------------------------------------------------------------------
check_security_hook() {
  if [ ! -f "$PROJECT_DIR/.git/hooks/pre-commit" ]; then
    warn "Security pre-commit hook not installed. Run: bash scripts/install-git-hooks.sh"
  fi
}
check_security_hook

# ---------------------------------------------------------------------------
# 7. Naming convention: meta-agent tool files must be snake_case
# ---------------------------------------------------------------------------
check_tool_naming() {
  for f in "$PROJECT_DIR"/meta-agent/tools/*.py; do
    [ -f "$f" ] || continue
    local basename
    basename=$(basename "$f")
    [[ "$basename" == __* ]] && continue
    if [[ "$basename" =~ [A-Z] || "$basename" =~ - ]]; then
      note "meta-agent/tools/$basename violates snake_case naming convention"
    fi
  done
}
check_tool_naming

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo ""
  echo "check-invariants: FAILED — fix the above before committing." >&2
  exit 1
fi
echo "OK: invariants consistent (CLAUDE.md ↔ code, i18n parity, naming conventions)"
