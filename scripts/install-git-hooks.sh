#!/usr/bin/env bash
# Install repo-managed git hooks. Run once after cloning.
#
# Prefers lefthook (runs all hooks defined in lefthook.yml). Falls back to
# manual symlinks if lefthook is not installed.
set -eu
cd "$(dirname "$0")/.."

chmod +x scripts/pre-commit-security.sh scripts/check-invariants.sh scripts/check-docs-agent.sh scripts/test.sh

if command -v lefthook >/dev/null 2>&1; then
  lefthook install
  echo "Installed git hooks via lefthook (lefthook.yml)"
  echo "  pre-commit: security patterns + invariants + eslint + bash syntax"
  echo "  pre-push:   full test suite + doc consistency (agent-based, advisory)"
else
  echo "lefthook not found — installing manual symlinks."
  echo "  (Install lefthook for the full hook suite: https://github.com/evilmartians/lefthook)"
  echo ""

  # Pre-commit: chain security + invariants
  cat > .git/hooks/pre-commit << 'HOOK'
#!/usr/bin/env bash
set -e
./scripts/pre-commit-security.sh
./scripts/check-invariants.sh
HOOK
  chmod +x .git/hooks/pre-commit
  echo "Installed pre-commit hook (security + invariants)"

  # Pre-push: test suite
  cat > .git/hooks/pre-push << 'HOOK'
#!/usr/bin/env bash
set -e
./scripts/test.sh
./scripts/check-docs-agent.sh
HOOK
  chmod +x .git/hooks/pre-push
  echo "Installed pre-push hook (test.sh + doc consistency)"
fi
