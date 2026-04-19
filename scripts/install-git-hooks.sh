#!/usr/bin/env bash
# Install repo-managed git hooks. Run once after cloning.
set -eu
cd "$(dirname "$0")/.."

ln -sf ../../scripts/pre-commit-security.sh .git/hooks/pre-commit
chmod +x scripts/pre-commit-security.sh

echo "Installed pre-commit hook → scripts/pre-commit-security.sh"
echo "Blocks commits that reintroduce Lambda AuthType=NONE or Principal:\"*\"."
